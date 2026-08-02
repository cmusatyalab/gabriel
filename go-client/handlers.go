package gabrielclient

import (
	"context"
	"fmt"
	"io"
	"sync"
	"time"

	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
	"github.com/rs/zerolog/log"
	"golang.org/x/sync/semaphore"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/proto"
)

// consumerHandler handles incoming messages from the server.
func (client *GrpcClient) consumerHandler(
	ctx context.Context,
	sessCancel context.CancelFunc,
	errCh chan error,
	wg *sync.WaitGroup) {
	defer wg.Done()
	for {
		if err := ctx.Err(); err != nil {
			return
		}
		toClient, err := client.stream.Recv()
		if err == io.EOF {
			errCh <- fmt.Errorf("%w: server closed the session", errDisconnected)
			sessCancel()
			return
		}
		if err != nil {
			if status.Code(err) == codes.Canceled {
				return // ctx was canceled; shutting down normally
			}
			errCh <- fmt.Errorf("%w: %v", errDisconnected, err)
			sessCancel()
			return
		}

		log.Debug().
			Str("message", prototext.Format(toClient)).
			Msg("received message from server")

		switch x := toClient.MessageType.(type) {
		case *gabrielpb.ToClient_Registered_:
			client.processRegistered(x.Registered)
		case *gabrielpb.ToClient_ResultWrapper_:
			client.processResult(x.ResultWrapper)
		case *gabrielpb.ToClient_EngineIdsUpdate_:
			log.Info().Msg("received engine ids update from server")
			engineIDs := make(map[string]struct{})
			for _, engineID := range x.EngineIdsUpdate.EngineIds {
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

// processRegistered processes the server's acknowledgement of this client's
// Registration message.
func (client *GrpcClient) processRegistered(registered *gabrielpb.ToClient_Registered) {
	log.Info().Msg("registered with server")
	client.numTokensPerProducer = int(registered.NumTokensPerProducer)
	client.engineIDMu.Lock()
	for _, engineID := range registered.EngineIds {
		client.engineIDs[engineID] = struct{}{}
	}
	client.engineIDMu.Unlock()

	for _, p := range client.inputProducers {
		client.tokenPool[p.Name] = &tokenPool{
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
		Strs("engine_ids", registered.EngineIds).
		Int("num_tokens_per_producer", int(registered.NumTokensPerProducer)).
		Msg("available engines")
}

// registrationHandler sends the client's Registration message and retries it
// on a fixed interval until the server acknowledges it with a Registered
// message (observed via client.connected, set by processRegistered).
func (client *GrpcClient) registrationHandler(
	ctx context.Context,
	sessCancel context.CancelFunc,
	errCh chan error,
	wg *sync.WaitGroup) {
	defer wg.Done()

	registration := &gabrielpb.FromClient_Registration{}
	if client.clientInfo != nil {
		registration.ClientInfo = client.clientInfo
	}
	fromClient := &gabrielpb.FromClient{
		MessageType: &gabrielpb.FromClient_Registration_{Registration: registration},
	}

	send := func() bool {
		if err := client.sendMsg(fromClient); err != nil {
			errCh <- fmt.Errorf("%w: error sending registration: %v", errDisconnected, err)
			sessCancel()
			return false
		}
		return true
	}

	if !send() {
		return
	}

	ticker := time.NewTicker(client.registrationRetryInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			client.connectedMu.Lock()
			connected := client.connected
			client.connectedMu.Unlock()
			if connected {
				return
			}
			log.Info().Msg("no registration acknowledgement yet; retrying")
			if !send() {
				return
			}
		}
	}
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
	sessCancel context.CancelFunc,
	errCh chan error,
	wg *sync.WaitGroup,
	producer *InputProducer) {
	logger := log.With().Str("producer", producer.Name).Logger()
	defer wg.Done()

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
			return // session ended or shutting down
		}
		if !producer.IsRunning() {
			logger.Info().Msg("producer is not running; waiting")
			producer.waitForRunning()
			logger.Info().Msg("producer resumed")
		}

		if err := tokenPool.GetToken(ctx); err != nil {
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

		input := &gabrielpb.FromClient_Input{}
		input.FrameId = int64(frameId)
		frameId += 1
		input.ProducerId = producer.Name

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
				client.fatalCancel()
				return
			}
			input.TargetEngineIds = append(input.TargetEngineIds, engineID)
		}
		input.InputFrame = inputFrame

		fromClient := &gabrielpb.FromClient{
			MessageType: &gabrielpb.FromClient_Input_{Input: input},
		}

		logger.Trace().
			Str("producer", producer.Name).
			Strs("engines", targetEngines).
			Msg("sending input to server")
		if err := client.sendMsg(fromClient); err != nil {
			errCh <- fmt.Errorf("%w: error sending message to server: %v", errDisconnected, err)
			logger.Err(err).Msg("error sending message to server")
			sessCancel()
			return
		}
	}
}
