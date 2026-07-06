package client

import (
	"context"
	"fmt"
	"io"
	"sync"

	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
	"github.com/rs/zerolog/log"
	"golang.org/x/sync/semaphore"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/proto"
)

// Producer is a function that produces input frames for the client. It takes a
// context.Context as input and returns a channel that produces
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

// Produce calls the underlying producer function to produce input frames. The
// returned channel will be closed when the given context is canceled.
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

// ChangeTargetEngines changes the target engines of the InputProducer to the
// given targetEngineIDs.
func (producer *InputProducer) ChangeTargetEngines(targetEngineIDs []string) {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	producer.targetEngineIDs = make(map[string]struct{})
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

func (pool *tokenPool) ResetTokens() {
	pool.sem = semaphore.NewWeighted(int64(pool.maxTokens))
}

func (pool *tokenPool) GetToken(ctx context.Context) error {
	return pool.sem.Acquire(ctx, 1)
}

func (pool *tokenPool) ReturnToken() {
	pool.sem.Release(1)
}

// Client defines the interface for a Gabriel client.
type Client interface {
	Launch(context.Context)
}

// GrpcClient implements the Client interface using gRPC for communication
// with the server. It relies on gRPC's own HTTP/2 keepalive for liveness
// detection rather than hand-rolled heartbeats, and it does not attempt to
// resume a session on disconnect: once the stream ends, the client shuts
// down.
type GrpcClient struct {
	// ServerEndpoint must be a valid gRPC target, e.g. "host:port" for TCP
	// or "unix:///path/to/socket" for a Unix domain socket.
	ServerEndpoint       string
	consumer             func(*gabrielpb.Result)
	tokenPool            map[string]tokenPool
	conn                 *grpc.ClientConn
	stream               grpc.BidiStreamingClient[gabrielpb.FromClient, gabrielpb.ToClient]
	streamMu             sync.Mutex
	connected            bool
	connectedMu          sync.Mutex
	connectedCond        *sync.Cond
	inputProducers       []*InputProducer
	numTokensPerProducer int
	engineIDs            map[string]struct{}
	engineIDMu           sync.Mutex
}

// NewGrpcClient creates a new GrpcClient with the given server endpoint and
// input producers.
func NewGrpcClient(
	serverEndpoint string,
	inputProducers []*InputProducer,
	consumer func(*gabrielpb.Result)) (*GrpcClient, error) {

	client := GrpcClient{
		ServerEndpoint: serverEndpoint,
		consumer:       consumer,
		inputProducers: inputProducers,
		tokenPool:      make(map[string]tokenPool),
		engineIDs:      make(map[string]struct{}),
	}
	client.connectedCond = sync.NewCond(&client.connectedMu)

	return &client, nil
}

func (client *GrpcClient) sendMsg(msg *gabrielpb.FromClient) error {
	client.streamMu.Lock()
	defer client.streamMu.Unlock()
	return client.stream.Send(msg)
}

// Launch starts the GrpcClient and connects to the Gabriel server. This
// function is non-blocking.
func (client *GrpcClient) Launch(ctx context.Context) (<-chan error, error) {
	ctx, cancel := context.WithCancel(ctx)
	log.Info().Str("endpoint", client.ServerEndpoint).Msg("connecting to server")
	conn, err := grpc.NewClient(
		client.ServerEndpoint,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		log.Err(err).Msg("error creating gRPC client")
		cancel()
		return nil, err
	}
	client.conn = conn

	stream, err := gabrielpb.NewGabrielServiceClient(conn).Session(ctx)
	if err != nil {
		log.Err(err).Msg("error opening session with server")
		client.conn.Close()
		cancel()
		return nil, err
	}
	client.stream = stream

	errCh := make(chan error, len(client.inputProducers)+1)
	for _, producer := range client.inputProducers {
		go client.producerHandler(ctx, cancel, errCh, producer)
	}
	go client.consumerHandler(ctx, cancel, errCh)

	go func() {
		// cleanup grpc client connection
		<-ctx.Done()
		client.conn.Close()
	}()

	return errCh, nil
}

// consumerHandler handles incoming messages from the server.
func (client *GrpcClient) consumerHandler(
	ctx context.Context,
	cancel context.CancelFunc,
	errCh chan error) {
	for {
		if err := ctx.Err(); err != nil {
			errCh <- err
			log.Err(err).Msg("consumer handler exited")
			return
		}
		toClient, err := client.stream.Recv()
		if err == io.EOF {
			errCh <- err
			log.Err(err).Msg("server closed the session")
			cancel()
			return
		}
		if err != nil {
			errCh <- err
			if status.Code(err) == codes.Canceled {
				return // ctx was canceled; shutting down normally
			}
			log.Err(err).Msg("received error from server")
			cancel()
			return
		}

		log.Info().
			Str("message", prototext.Format(toClient)).
			Msg("received message from server")

		switch x := toClient.MessageType.(type) {
		case *gabrielpb.ToClient_Welcome_:
			client.processWelcome(x.Welcome)
		case *gabrielpb.ToClient_ResultWrapper_:
			client.processResult(x.ResultWrapper)
		case *gabrielpb.ToClient_Control_:
			log.Info().Msg("received control message from server")
			engineIDs := make(map[string]struct{})
			for _, engineID := range x.Control.EngineIds {
				engineIDs[engineID] = struct{}{}
			}
			client.engineIDMu.Lock()
			client.engineIDs = engineIDs
			client.engineIDMu.Unlock()
		case nil:
			log.Error().Msg("could not decode message type")
		default:
			log.Error().Msg("could not decode message type")
		}
	}
}

// processWelcome processes the welcome message from the server.
func (client *GrpcClient) processWelcome(welcome *gabrielpb.ToClient_Welcome) {
	log.Info().Msg("received welcome from server")
	client.numTokensPerProducer = int(welcome.NumTokensPerProducer)
	client.engineIDMu.Lock()
	for _, engineID := range welcome.EngineIds {
		client.engineIDs[engineID] = struct{}{}
	}
	client.engineIDMu.Unlock()

	for _, p := range client.inputProducers {
		client.tokenPool[p.Name] = tokenPool{
			sem:          semaphore.NewWeighted(int64(client.numTokensPerProducer)),
			maxTokens:    client.numTokensPerProducer,
			producerName: p.Name,
		}
	}

	client.connectedMu.Lock()
	client.connected = true
	client.connectedCond.Broadcast()
	client.connectedMu.Unlock()

	log.Info().
		Strs("engine_ids", welcome.EngineIds).
		Int("num_tokens_per_producer", int(welcome.NumTokensPerProducer)).
		Msg("available engines")
}

// processResult processes results from the server.
func (client *GrpcClient) processResult(resultWrapper *gabrielpb.ToClient_ResultWrapper) {
	result := resultWrapper.Result
	resultStatus := result.Status
	code := resultStatus.Code
	msg := resultStatus.Message
	log.Debug().Str("engine_id", result.TargetEngineId).Msg("processing result from engine")

	switch code {
	case gabrielpb.StatusCode_SUCCESS:
		client.consumer(result)

	case gabrielpb.StatusCode_NO_ENGINE_FOR_INPUT:
		log.Error().Str("message", msg).Msg("no engine for input")

	case gabrielpb.StatusCode_SERVER_DROPPED_FRAME:
		log.Error().
			Str("engine_id", result.TargetEngineId).
			Str("producer_id", resultWrapper.ProducerId).
			Str("message", msg).
			Msg("engine dropped frame")

	default:
		log.Error().
			Str("producer_id", resultWrapper.ProducerId).
			Str("engine_id", result.TargetEngineId).
			Str("code", code.String()).
			Str("message", msg).
			Msg("input caused error")
	}

	if resultWrapper.ReturnToken {
		producerID := resultWrapper.ProducerId
		if pool, ok := client.tokenPool[producerID]; ok {
			pool.ReturnToken()
		} else {
			log.Error().Msgf("failed to return token, producer id %s does not exist", producerID)
		}
	}
}

// producerHandler handles input production for a single InputProducer.
func (client *GrpcClient) producerHandler(
	ctx context.Context,
	cancel context.CancelFunc,
	errCh chan error,
	producer *InputProducer) {
	logger := log.With().Str("producer", producer.Name).Logger()

	client.connectedMu.Lock()
	for !client.connected {
		client.connectedCond.Wait()
	}
	client.connectedMu.Unlock()

	tokenPool := client.tokenPool[producer.Name]

	frameId := 1
	resultCh := producer.Produce(ctx)

	for {
		if err := ctx.Err(); err != nil {
			errCh <- err
			logger.Err(err).Msg("producer exited")
			return
		}
		if !producer.IsRunning() {
			logger.Info().Msg("producer is not running; waiting")
			producer.WaitForRunning()
			logger.Info().Msg("producer resumed")
		}

		if err := tokenPool.GetToken(ctx); err != nil {
			errCh <- err
			return // ctx was canceled
		}

		var inputFrame *gabrielpb.InputFrame
		select {
		case <-ctx.Done():
			logger.Info().Msg("producer handler exited, context done")
			return
		case inputFrame = <-resultCh:
		}

		if inputFrame == nil {
			logger.Error().Msg("received nil frame from producer")
			tokenPool.ReturnToken()
			continue
		}
		if proto.Size(inputFrame) == 0 {
			logger.Error().Msg("producer produced empty frame")
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
				errCh <- fmt.Errorf("engine %s not connected to the server", engineID)
				logger.Error().
					Str("engine_id", engineID).
					Msg("attempt to target engine that is not connected to the server")
				cancel()
				return
			}
			fromClient.TargetEngineIds = append(fromClient.TargetEngineIds, engineID)
		}
		fromClient.InputFrame = inputFrame

		logger.Trace().Str("producer", producer.Name).Msg("sending input to server")
		if err := client.sendMsg(fromClient); err != nil {
			errCh <- fmt.Errorf("error sending message to server: %w", err)
			logger.Err(err).Msg("error sending message to server")
			cancel()
			return
		}
	}
}
