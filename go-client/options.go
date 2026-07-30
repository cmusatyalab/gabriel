package gabrielclient

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/keepalive"
)

// DefaultReconnectInterval is the default fixed delay between attempts to
// reestablish the gRPC stream after it is disconnected from the server, used
// unless overridden with WithReconnectInterval.
const DefaultReconnectInterval = 2 * time.Second

// DefaultKeepaliveTime is the default interval between HTTP/2 keepalive pings
// sent to the server when the connection would otherwise be idle, used unless
// overridden with WithDialOptions(grpc.WithKeepaliveParams(...)).
const DefaultKeepaliveTime = 10 * time.Second

// DefaultKeepaliveTimeout is the default time to wait for a keepalive ping
// acknowledgement before the connection is considered dead, used unless
// overridden with WithDialOptions(grpc.WithKeepaliveParams(...)).
const DefaultKeepaliveTimeout = 5 * time.Second

// defaultKeepaliveDialOption is applied first when dialing, so any
// grpc.WithKeepaliveParams passed via WithDialOptions takes precedence over
// it.
var defaultKeepaliveDialOption = grpc.WithKeepaliveParams(keepalive.ClientParameters{
	Time:                DefaultKeepaliveTime,
	Timeout:             DefaultKeepaliveTimeout,
	PermitWithoutStream: true,
})

// LoadTLSCredentials builds gRPC transport credentials from PEM files for use
// with WithTLSCredentials.
//
// caCertPath verifies the server's certificate. If empty, the system's default
// trust store is used instead. clientCertPath/clientKeyPath present a client
// certificate for mutual TLS and must be given together. If both are empty, no
// client certificate is presented.
func LoadTLSCredentials(
	caCertPath, clientCertPath, clientKeyPath string,
) (credentials.TransportCredentials, error) {
	tlsConfig := &tls.Config{}

	if caCertPath != "" {
		caCert, err := os.ReadFile(caCertPath)
		if err != nil {
			return nil, fmt.Errorf("reading CA cert: %w", err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(caCert) {
			return nil, fmt.Errorf("failed to parse CA cert %s", caCertPath)
		}
		tlsConfig.RootCAs = pool
	}

	if clientCertPath != "" && clientKeyPath != "" {
		cert, err := tls.LoadX509KeyPair(clientCertPath, clientKeyPath)
		if err != nil {
			return nil, fmt.Errorf("loading client cert/key: %w", err)
		}
		tlsConfig.Certificates = []tls.Certificate{cert}
	}

	return credentials.NewTLS(tlsConfig), nil
}

// Option configures a GrpcClient. Options are applied in the order given to
// NewGrpcClient.
type Option func(*GrpcClient)

// WithTLSCredentials configures the GrpcClient to secure its connection to the
// server with the given transport credentials (e.g. from LoadTLSCredentials).
// If not provided, the client dials in plaintext.
func WithTLSCredentials(creds credentials.TransportCredentials) Option {
	return func(client *GrpcClient) {
		client.tlsCredentials = creds
	}
}

// WithReconnectInterval configures the fixed delay between attempts to
// reestablish the gRPC stream after it is disconnected from the server. If not
// provided, DefaultReconnectInterval is used.
func WithReconnectInterval(interval time.Duration) Option {
	return func(client *GrpcClient) {
		client.reconnectInterval = interval
	}
}

// WithDialOptions appends additional gRPC dial options to those used when
// connecting to the server.
func WithDialOptions(dialOptions ...grpc.DialOption) Option {
	return func(client *GrpcClient) {
		client.dialOptions = append(client.dialOptions, dialOptions...)
	}
}
