package gabrielclient

import (
	"context"
	"errors"
	"sync"
	"time"

	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
	"github.com/rs/zerolog/log"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/types/known/anypb"
)

// errDisconnected wraps errors that indicate the gRPC stream to the server was
// lost.
var errDisconnected = errors.New("disconnected from server")

// Client defines the interface for a Gabriel client.
type Client interface {
	Launch(context.Context) (<-chan error, error)
}

// GrpcClient implements the Client interface using gRPC for communication with
// the server. It configures HTTP/2 keepalive pings for liveness detection, so
// a dead connection is noticed even without application traffic.
type GrpcClient struct {
	// serverEndpoint must be a valid gRPC target, e.g. "host:port" for TCP
	// or "unix:///path/to/socket" for a Unix domain socket.
	serverEndpoint string
	// tlsCredentials, if set, are used to secure the connection to the server.
	// If nil, an insecure (plaintext) connection is used.
	tlsCredentials       credentials.TransportCredentials
	reconnectInterval    time.Duration
	dialOptions          []grpc.DialOption
	consumer             func(*gabrielpb.Result)
	tokenPool            map[string]*tokenPool
	fatalCancel          context.CancelFunc
	conn                 *grpc.ClientConn
	stream               grpc.BidiStreamingClient[gabrielpb.FromClient, gabrielpb.ToClient]
	streamMu             sync.Mutex
	connected            bool
	connectedMu          sync.Mutex
	connectedCond        *sync.Cond
	inputSources         []*InputSource
	numTokensPerProducer int
	engineIDs            map[string]struct{}
	engineIDMu           sync.Mutex
	// clientInfo, if set, is sent to the server once per session as part of
	// the client's Registration message.
	clientInfo *anypb.Any
	// registrationRetryInterval is the fixed delay between attempts to
	// register with the server at the start of each session.
	registrationRetryInterval time.Duration
}

// NewGrpcClient creates a new GrpcClient with the given server endpoint and
// input producers. Additional behavior, such as TLS credentials or the
// reconnect interval, can be configured via Option values.
func NewGrpcClient(
	serverEndpoint string,
	inputSources []*InputSource,
	consumer func(*gabrielpb.Result),
	opts ...Option) *GrpcClient {

	client := GrpcClient{
		serverEndpoint:            serverEndpoint,
		consumer:                  consumer,
		inputSources:              inputSources,
		tokenPool:                 make(map[string]*tokenPool),
		engineIDs:                 make(map[string]struct{}),
		reconnectInterval:         DefaultReconnectInterval,
		registrationRetryInterval: DefaultRegistrationRetryInterval,
	}
	client.connectedCond = sync.NewCond(&client.connectedMu)

	for _, opt := range opts {
		opt(&client)
	}

	return &client
}

func (client *GrpcClient) sendMsg(msg *gabrielpb.FromClient) error {
	client.streamMu.Lock()
	defer client.streamMu.Unlock()
	return client.stream.Send(msg)
}

// Launch starts the GrpcClient and connects to the Gabriel server. This
// function is non-blocking. Once connected, if the gRPC stream to the server
// is disconnected, the client automatically attempts to reestablish the
// connection every client.reconnectInterval until ctx is canceled.
func (client *GrpcClient) Launch(ctx context.Context) (<-chan error, error) {
	ctx, cancel := context.WithCancel(ctx)
	client.fatalCancel = cancel

	if err := client.connect(ctx); err != nil {
		cancel()
		return nil, err
	}

	errCh := make(chan error, len(client.inputSources)+1)

	go func() {
		defer close(errCh)
		defer cancel()
		defer client.conn.Close()

		for {
			fatalErr, disconnected := client.runSession(ctx)
			if fatalErr != nil {
				errCh <- fatalErr
				return
			}
			if ctx.Err() != nil {
				return
			}
			if !disconnected {
				return
			}

			if err := client.reconnect(ctx); err != nil {
				return // ctx was canceled while reconnecting
			}
		}
	}()

	return errCh, nil
}

// connect dials the Gabriel server and opens the ClientSession stream,
// storing the resulting connection and stream on the client.
func (client *GrpcClient) connect(ctx context.Context) error {
	log.Info().Str("endpoint", client.serverEndpoint).Msg("connecting to server")
	transportCredentials := client.tlsCredentials
	if transportCredentials == nil {
		transportCredentials = insecure.NewCredentials()
	}
	dialOptions := append(
		[]grpc.DialOption{
			grpc.WithTransportCredentials(transportCredentials),
			defaultKeepaliveDialOption,
		},
		client.dialOptions...,
	)
	conn, err := grpc.NewClient(client.serverEndpoint, dialOptions...)
	if err != nil {
		log.Err(err).Msg("error creating gRPC client")
		return err
	}

	stream, err := gabrielpb.NewGabrielClientServiceClient(conn).ClientSession(ctx)
	if err != nil {
		log.Err(err).Msg("error opening session with server")
		conn.Close()
		return err
	}

	client.conn = conn
	client.stream = stream
	return nil
}

// reconnect waits client.reconnectInterval and then repeatedly attempts to
// reconnect to the server at that fixed interval until a connection is
// established or ctx is canceled.
func (client *GrpcClient) reconnect(ctx context.Context) error {
	for {
		log.Info().
			Dur("reconnect_interval", client.reconnectInterval).
			Msg("disconnected from server; will attempt to reconnect")
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(client.reconnectInterval):
		}

		if err := client.connect(ctx); err != nil {
			log.Err(err).Msg("error reconnecting to server; will retry")
			continue
		}
		return nil
	}
}

// runSession runs a single gRPC stream session with the server. It resets
// per-session state, starts the producer and consumer handlers, and waits for
// them to finish. It returns a non-nil fatalErr if the client should stop
// entirely, or disconnected=true if the session ended because the stream was
// lost and should be retried.
func (client *GrpcClient) runSession(
	ctx context.Context) (fatalErr error, disconnected bool) {
	sessCtx, sessCancel := context.WithCancel(ctx)
	defer sessCancel()

	client.connectedMu.Lock()
	client.connected = false
	client.connectedMu.Unlock()
	client.engineIDMu.Lock()
	client.engineIDs = make(map[string]struct{})
	client.engineIDMu.Unlock()

	sessErrCh := make(chan error, len(client.inputSources)+2)
	var wg sync.WaitGroup

	for _, producer := range client.inputSources {
		wg.Add(1)
		go client.producerHandler(sessCtx, sessCancel, sessErrCh, &wg, producer)
	}
	wg.Add(1)
	go client.consumerHandler(sessCtx, sessCancel, sessErrCh, &wg)
	wg.Add(1)
	go client.registrationHandler(sessCtx, sessCancel, sessErrCh, &wg)

	go func() {
		wg.Wait()
		close(sessErrCh)
	}()

	for err := range sessErrCh {
		if errors.Is(err, errDisconnected) {
			disconnected = true
			continue
		}
		fatalErr = err
	}

	return fatalErr, disconnected
}

var _ Client = (*GrpcClient)(nil)
