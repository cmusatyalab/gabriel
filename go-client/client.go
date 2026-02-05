package client

import (
	"bytes"
	"context"
	"log"
	"sync"
	"sync/atomic"
	"time"

	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
	"github.com/golang/glog"
	zmq "github.com/pebbe/zmq4"
	"golang.org/x/sync/semaphore"
	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/proto"
)

const HelloMsg = "Hello message"
const HeartbeatMsg = ""
const ServerTimeoutSecs = 10
const HeartbeatIntervalSecs = 1 * time.Second

// Producer is a function that produces input frames for the client. It takes
// a context.Context as input and returns a channel that produces
// *gabrielpb.InputFrame. The Producer must stop producing and close the
// channel when the context is canceled.
type Producer func(ctx context.Context) <-chan *gabrielpb.InputFrame

type InputProducer struct {
	Name            string
	producer        Producer
	targetEngineIDs map[string]struct{}
	running         bool
	engineMu        sync.Mutex
	runningMu       sync.Mutex
	cond            *sync.Cond
}

// NewInputProducer creates a new InputProducer with the given name and
// producer. The resulting InputProducer will target engines specified by
// targetEngineIDs.
func NewInputProducer(name string, producer Producer, targetEngineIDs []string) *InputProducer {
	inputProducer := &InputProducer{
		Name:            name,
		producer:        producer,
		targetEngineIDs: make(map[string]struct{}),
		running:         true,
	}
	for _, engineID := range targetEngineIDs {
		inputProducer.targetEngineIDs[engineID] = struct{}{}
	}
	inputProducer.cond = sync.NewCond(&inputProducer.runningMu)
	return inputProducer
}

// Produce calls the underlying producer function to produce input frames.
// The returned channel will be closed when the given context is canceled.
func (producer *InputProducer) Produce(ctx context.Context) <-chan *gabrielpb.InputFrame {
	return producer.producer(ctx)
}

// Resume resumes the InputProducer to produce input frames.
func (producer *InputProducer) Resume() {
	producer.runningMu.Lock()
	defer producer.runningMu.Unlock()
	producer.running = true
	producer.cond.Signal()
}

// ChangeTargetEngines changes the target engines of the InputProducer to
// the given targetEngineIDs.
func (producer *InputProducer) ChangeTargetEngines(targetEngineIDs []string) {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	for _, engineID := range targetEngineIDs {
		producer.targetEngineIDs[engineID] = struct{}{}
	}
}

// AddTargetEngine adds the given engineID to the target engines of the
// InputProducer.
func (producer *InputProducer) AddTargetEngine(engineID string) {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	producer.targetEngineIDs[engineID] = struct{}{}
}

// RemoveTargetEngine removes the given engineID from the target engines of the
// InputProducer.
func (producer *InputProducer) RemoveTargetEngine(engineID string) {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	delete(producer.targetEngineIDs, engineID)
}

// IsRunning returns true if the InputProducer is running.
func (producer *InputProducer) IsRunning() bool {
	producer.runningMu.Lock()
	defer producer.runningMu.Unlock()
	return producer.running
}

// WaitForRunning blocks until the InputProducer is running.
func (producer *InputProducer) WaitForRunning() {
	producer.runningMu.Lock()
	defer producer.runningMu.Unlock()
	for !producer.running {
		producer.cond.Wait()
	}
}

// TargetEngineIDs returns the target engine IDs of the InputProducer.
func (producer *InputProducer) TargetEngineIDs() []string {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	engineIDs := make([]string, 0, len(producer.targetEngineIDs))
	for engineID := range producer.targetEngineIDs {
		engineIDs = append(engineIDs, engineID)
	}
	return engineIDs
}

// tokenPool manages the tokens for a single InputProducer.
type tokenPool struct {
	sem          *semaphore.Weighted
	maxTokens    int
	producerName string
}

func (pool tokenPool) ResetTokens() {
	pool.sem = semaphore.NewWeighted(int64(pool.maxTokens))
}

func (pool tokenPool) GetToken(ctx context.Context) error {
	return pool.sem.Acquire(ctx, 1)
}

func (pool tokenPool) ReturnToken() {
	pool.sem.Release(1)
}

// Client defines the interface for a Gabriel client.
type Client interface {
	Launch(context.Context)
}

// ZeroMQClient implements the Client interface using ZeroMQ for communication
// with the server.
type ZeroMQClient struct {
	ServerEndpoint       string
	consumer             func(*gabrielpb.Result)
	tokenPool            map[string]tokenPool
	socket               *zmq.Socket
	connected            bool
	connectedMu          sync.Mutex
	connectedCond        *sync.Cond
	inputProducers       []*InputProducer
	numTokensPerProducer int
	engineIDs            map[string]struct{}
	engineIDMu           sync.Mutex
	pendingHeartbeat     atomic.Bool
	lastHeartbeatTime    time.Time
	heartbeatCh          chan struct{}
}

// NewZeroMQClient creates a new ZeroMQClient with the given server endpoint
// and input producers. serverEndpoint can be in the format of
// "tcp://<ip>:<port>" or "ipc://<path>".
func NewZeroMQClient(
	serverEndpoint string,
	inputProducers []*InputProducer,
	consumer func(*gabrielpb.Result)) (*ZeroMQClient, error) {

	client := ZeroMQClient{
		ServerEndpoint: serverEndpoint,
		consumer:       consumer,
		inputProducers: inputProducers,
		heartbeatCh:    make(chan struct{}, 1),
		tokenPool:      make(map[string]tokenPool),
		engineIDs:      make(map[string]struct{}),
	}
	client.connectedCond = sync.NewCond(&client.connectedMu)

	return &client, nil
}

// Launch starts the ZeroMQClient and connects to the server.
func (client *ZeroMQClient) Launch(ctx context.Context) {
	glog.Infoln("Connecting to server at", client.ServerEndpoint)
	// Connect to the server and send hello message.
	var err error
	client.socket, err = zmq.NewSocket(zmq.DEALER)
	if err != nil {
		log.Fatalln("Error creating dealer socket:", err)
	}
	err = client.socket.Connect(client.ServerEndpoint)
	if err != nil {
		log.Fatalln("Error connecting to server:", err)
	}
	_, err = client.socket.Send(HelloMsg, 0)
	if err != nil {
		log.Fatalln("Error sending hello msg to server:", err)
	}
	glog.Infoln("Sent hello message to server")

	// Start subroutines.
	for _, producer := range client.inputProducers {
		go client.producerHandler(ctx, producer)
	}
	go client.consumerHandler(ctx)

	<-ctx.Done()
	// cleanup
	client.socket.Close()
}

// consumerHandler handles incoming messages from the server.
func (client *ZeroMQClient) consumerHandler(ctx context.Context) {
	poller := zmq.NewPoller()
	poller.Add(client.socket, zmq.POLLIN)

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		now := time.Now()
		polled, err := poller.Poll(ServerTimeoutSecs * time.Second)
		if err != nil {
			glog.Fatalln("Received error", err, "when polling")
		}
		if len(polled) == 0 {
			glog.Infoln("Socket polling took", time.Since(now))
			client.connectedMu.Lock()
			if client.connected {
				client.connected = false
				client.connectedMu.Unlock()
				glog.Fatalln("Disconnected from server")
			} else {
				client.connectedMu.Unlock()
				glog.Infoln("Still disconnected; reconnecting and resending heartbeat")
			}
			poller.Remove(0)
			client.socket.Close()
			client.socket, err = zmq.NewSocket(zmq.DEALER)
			if err != nil {
				log.Fatalln("Error creating dealer socket:", err)
			}
			client.sendHeartbeat(true /* force */)
			poller.Add(client.socket, zmq.POLLIN)
			continue
		}

		reply, err := client.socket.RecvMessageBytes(0)
		if err != nil {
			glog.Fatalln(err)
		}

		client.connectedMu.Lock()
		if !client.connected {
			client.connected = true
			client.connectedMu.Unlock()
			glog.Infoln("Reconnected to server")
			for t := range client.tokenPool {
				client.tokenPool[t].ResetTokens()
			}
		} else {
			client.connectedMu.Unlock()
		}

		if bytes.Equal(reply[0], []byte(HeartbeatMsg)) {
			glog.V(1).Infoln("Received heartbeat from server")
			client.pendingHeartbeat.Store(false)
			continue
		}

		toClient := &gabrielpb.ToClient{}

		if err := proto.Unmarshal(reply[0], toClient); err != nil {
			glog.Fatalln(err)
		}

		glog.Infoln("Received message from server:", prototext.Format(toClient))

		switch x := toClient.MessageType.(type) {
		case *gabrielpb.ToClient_Welcome_:
			client.processWelcome(x.Welcome)
		case *gabrielpb.ToClient_ResultWrapper_:
			client.processResult(x.ResultWrapper)
		case *gabrielpb.ToClient_Control_:
			glog.Infoln("Received control message from server")
			engineIDs := make(map[string]struct{})
			for _, engineID := range x.Control.EngineIds {
				engineIDs[engineID] = struct{}{}
			}
			client.engineIDMu.Lock()
			client.engineIDs = engineIDs
			client.engineIDMu.Unlock()
		case nil:
			glog.Fatalln("Could not decode message type")
		default:
			glog.Fatalln("Could not decode message type")
		}
	}
}

// processWelcome processes the welcome message from the server.
func (client *ZeroMQClient) processWelcome(welcome *gabrielpb.ToClient_Welcome) {
	glog.Infoln("Received welcome from server")
	client.numTokensPerProducer = int(welcome.NumTokensPerProducer)
	for _, engineID := range welcome.EngineIds {
		client.engineIDs[engineID] = struct{}{}
	}
	client.connectedMu.Lock()
	client.connected = true
	client.connectedCond.Signal()
	client.connectedMu.Unlock()
	glog.Infoln(
		"Available engines:",
		welcome.EngineIds,
		"; number of tokens per producer:",
		welcome.NumTokensPerProducer)
}

// processResult processes results from the server.
func (client *ZeroMQClient) processResult(resultWrapper *gabrielpb.ToClient_ResultWrapper) {
	result := resultWrapper.Result
	resultStatus := result.Status
	code := resultStatus.Code
	msg := resultStatus.Message
	glog.V(1).Infoln("Processing result from engine", result.TargetEngineId)

	switch code {
	case gabrielpb.StatusCode_SUCCESS:
		client.consumer(result)

	case gabrielpb.StatusCode_NO_ENGINE_FOR_INPUT:
		glog.Fatalln("No engine for input: ", msg)

	case gabrielpb.StatusCode_SERVER_DROPPED_FRAME:
		glog.Errorf(
			"Engine %s dropped frame from producer %s: %s",
			result.TargetEngineId,
			resultWrapper.ProducerId,
			msg)

	default:
		glog.Errorf(
			"Input from producer %s targeting engine %s caused error %s: %s",
			resultWrapper.ProducerId,
			result.TargetEngineId,
			code,
			msg)
	}

	if resultWrapper.ReturnToken {
		producerID := resultWrapper.ProducerId
		client.tokenPool[producerID].ReturnToken()
	}
}

// producerHandler handles input production for a single InputProducer.
func (client *ZeroMQClient) producerHandler(ctx context.Context, producer *InputProducer) {
	client.connectedMu.Lock()
	for !client.connected {
		client.connectedCond.Wait()
	}
	client.connectedMu.Unlock()
	client.tokenPool[producer.Name] = tokenPool{
		sem:          semaphore.NewWeighted(int64(client.numTokensPerProducer)),
		maxTokens:    client.numTokensPerProducer,
		producerName: producer.Name,
	}
	frameId := 1

	var producerCtx context.Context
	var producerCancel context.CancelFunc
	var resultCh <-chan *gabrielpb.InputFrame
	var pendingTask bool

	tokenPool := client.tokenPool[producer.Name]

	for {
		if _, ok := client.tokenPool[producer.Name]; !ok {
			break
		}
		if !producer.IsRunning() {
			glog.Infoln("Producer", producer.Name, "is not running; waiting")
			producer.WaitForRunning()
			glog.Infoln("Producer", producer.Name, "resumed")
		}
		semCtx, cancel := context.WithTimeout(ctx, 1*time.Second)
		glog.V(2).Infoln("Producer", producer.Name, "waiting for token")
		err := tokenPool.GetToken(semCtx)
		glog.V(2).Infoln("Producer", producer.Name, "obtained token")
		cancel()
		// Failed to acquire semaphore because timeout was reached.
		if err != nil {
			go client.sendHeartbeat(false /* force */)
			continue
		}

		// Check if still connected to server
		client.connectedMu.Lock()
		if !client.connected && pendingTask {
			// Cancel the existing producer goroutine
			client.connectedMu.Unlock()
			producerCancel()
			pendingTask = false
			client.connectedMu.Lock()
		}
		// Wait for reconnection
		for !client.connected {
			client.connectedCond.Wait()
		}
		client.connectedMu.Unlock()

		if !pendingTask {
			glog.V(2).Infoln("Creating new producer task")
			producerCtx, producerCancel = context.WithCancel(ctx)
			resultCh = producer.Produce(producerCtx)
			pendingTask = true
		}
		var inputFrame *gabrielpb.InputFrame

		// Wait for the producer to produce an input. Time out to send
		// a heartbeat to the server.
		select {
		case inputFrame = <-resultCh:
			glog.V(2).Info("Got input from producer")
			pendingTask = false
			producerCancel()
			if inputFrame == nil {
				glog.Infoln("Received None from producer", producer.Name)
				tokenPool.ReturnToken()
				continue
			}

		case <-time.After(1 * time.Second):
			// send heartbeat
			tokenPool.ReturnToken()
			client.sendHeartbeat(false /* force */)
			glog.Infoln("Timed out waiting for input from producer", producer.Name)
			continue
		}

		if proto.Size(inputFrame) == 0 {
			glog.Errorln("Producer ", producer.Name, " produced empty frame")
			tokenPool.ReturnToken()
			continue
		}

		fromClient := &gabrielpb.FromClient{}
		fromClient.FrameId = int64(frameId)
		frameId += 1
		fromClient.ProducerId = producer.Name

		targetEngines := producer.TargetEngineIDs()
		client.engineIDMu.Lock()
		availableEngines := client.engineIDs
		client.engineIDMu.Unlock()

		for _, engineID := range targetEngines {
			if _, ok := availableEngines[engineID]; !ok {
				glog.Fatalln(
					"Attempt to target engine that is not connected to the server:",
					engineID)
			}
			fromClient.TargetEngineIds = append(fromClient.TargetEngineIds, engineID)
		}
		fromClient.InputFrame = inputFrame

		glog.V(2).Infoln("Sending input to server; producer=", producer.Name)
		client.sendToServer(fromClient)
	}
}

// sendToServer sends the given FromClient message to the server.
func (client *ZeroMQClient) sendToServer(fromClient *gabrielpb.FromClient) {
	msg, err := proto.Marshal(fromClient)
	if err != nil {
		glog.Fatalln(err)
	}
	_, err = client.socket.SendBytes(msg, 0)
	if err != nil {
		glog.Fatalln("Error sending message to server:", err)
	}
}

func (client *ZeroMQClient) sendHeartbeat(force bool) {
	if time.Since(client.lastHeartbeatTime) < HeartbeatIntervalSecs ||
		client.pendingHeartbeat.Swap(true) {
		return
	}
	glog.V(2).Infoln("Sending heartbeat to server")
	client.lastHeartbeatTime = time.Now()
	_, err := client.socket.SendBytes([]byte(HeartbeatMsg), 0)
	if err != nil {
		glog.Fatalln("Error sending heartbeat to server:", err)
	}
}
