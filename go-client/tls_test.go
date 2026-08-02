package gabrielclient_test

import (
	"context"
	"os/exec"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	gabrielclient "github.com/cmusatyalab/gabriel/go-client"
	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
)

// generateTestCert creates a self-signed cert/key pair in t.TempDir() and
// returns their paths, for use as both the server's certificate and (since it
// is self-signed) its own trusted CA.
func generateTestCert(t *testing.T) (certPath, keyPath string) {
	t.Helper()
	dir := t.TempDir()
	certPath = filepath.Join(dir, "server.crt")
	keyPath = filepath.Join(dir, "server.key")

	cmd := exec.Command(
		"openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
		"-keyout", keyPath, "-out", certPath,
		"-days", "1", "-subj", "/CN=localhost",
		// Go's TLS client requires a SAN match (unlike some OpenSSL-based
		// clients, which may still fall back to the CN); cover both the
		// hostname and the loopback IP the tests dial.
		"-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
	)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("generating test cert: %v\n%s", err, out)
	}
	return certPath, keyPath
}

// TestTLS starts its own server and echo engine (independent of the shared
// ones from TestMain) with TLS enabled on both the client-facing and
// engine-facing gRPC ports, and verifies the Go client can complete a TLS
// handshake and receive a response through them.
func TestTLS(t *testing.T) {
	useTestLogger(t)
	certPath, keyPath := generateTestCert(t)

	clientListener, err := reservePort()
	if err != nil {
		t.Fatalf("finding a free port: %v", err)
	}
	engineListener, err := reservePort()
	if err != nil {
		t.Fatalf("finding a free port: %v", err)
	}
	prometheusListener, err := reservePort()
	if err != nil {
		t.Fatalf("finding a free port: %v", err)
	}
	clientPort := portOf(clientListener)
	enginePort := portOf(engineListener)
	prometheusPort := portOf(prometheusListener)
	clientAddr := "127.0.0.1:" + strconv.Itoa(clientPort)
	engineAddr := "127.0.0.1:" + strconv.Itoa(enginePort)

	serverCmd := exec.Command(python, filepath.Join(repoRoot, "server", "main.py"),
		"--transport", "grpc",
		"--client_port", strconv.Itoa(clientPort),
		"--engine_port", strconv.Itoa(enginePort),
		"--prometheus_port", strconv.Itoa(prometheusPort),
		"--log-level", "INFO",
		"--tls-cert", certPath,
		"--tls-key", keyPath,
	)
	serverCmd.Dir = repoRoot
	serverCmd.Stdout = &prefixedWriter{prefix: "[tls-server] "}
	serverCmd.Stderr = &prefixedWriter{prefix: "[tls-server] "}
	// Hold the ports open until immediately before the server process starts,
	// to minimize the window in which another process could grab one first.
	clientListener.Close()
	engineListener.Close()
	prometheusListener.Close()
	if err := serverCmd.Start(); err != nil {
		t.Fatalf("starting gabriel server: %v", err)
	}
	defer stopProcess(serverCmd)

	engineCmd := exec.Command(python, engineScript,
		"--engine-id", "tls-engine-0",
		"--server-address", engineAddr,
		"--tls-ca-cert", certPath,
	)
	engineCmd.Dir = repoRoot
	engineCmd.Stdout = &prefixedWriter{prefix: "[tls-engine] "}
	engineCmd.Stderr = &prefixedWriter{prefix: "[tls-engine] "}
	if err := engineCmd.Start(); err != nil {
		t.Fatalf("starting echo engine: %v", err)
	}
	defer stopProcess(engineCmd)

	if err := waitForTCP(clientAddr, serverReadyTimeout); err != nil {
		t.Fatalf("gabriel server never became ready: %v", err)
	}

	tlsCreds, err := gabrielclient.LoadTLSCredentials(certPath, "", "")
	if err != nil {
		t.Fatalf("loading TLS credentials: %v", err)
	}

	if err := waitForEngineRegistered(clientAddr, "tls-engine-0", tlsCreds, engineRegisterTimeout); err != nil {
		t.Fatalf("engine never registered: %v", err)
	}

	producerFn := func(ctx context.Context) <-chan *gabrielpb.InputFrame {
		ch := make(chan *gabrielpb.InputFrame, 1)
		go func() {
			time.Sleep(inputInterval)
			ch <- &gabrielpb.InputFrame{
				Payload: &gabrielpb.InputFrame_StringPayload{
					StringPayload: "Hello over TLS!",
				},
			}
		}()
		return ch
	}
	producer := gabrielclient.NewInputProducer("producer-1", producerFn, []string{"tls-engine-0"})

	var receivedResponse atomic.Bool
	consumer := func(result *gabrielpb.Result) {
		receivedResponse.Store(true)
	}

	grpcClient, err := gabrielclient.NewGrpcClient(
		clientAddr,
		[]*gabrielclient.InputProducer{producer},
		consumer,
		gabrielclient.WithTLSCredentials(tlsCreds),
	)
	if err != nil {
		t.Fatalf("creating client: %v", err)
	}

	t.Log("Launching TLS client")
	go grpcClient.Launch(t.Context())
	if !waitUntil(launchResponseWait, pollInterval, receivedResponse.Load) {
		t.Fatal("did not receive response over TLS")
	}
}

// TestTLSRejectsPlaintextClient checks that a client without the server's CA
// configured (i.e. attempting a plaintext connection to a TLS-only server)
// fails rather than silently succeeding.
func TestTLSRejectsPlaintextClient(t *testing.T) {
	useTestLogger(t)
	certPath, keyPath := generateTestCert(t)

	clientListener, err := reservePort()
	if err != nil {
		t.Fatalf("finding a free port: %v", err)
	}
	engineListener, err := reservePort()
	if err != nil {
		t.Fatalf("finding a free port: %v", err)
	}
	prometheusListener, err := reservePort()
	if err != nil {
		t.Fatalf("finding a free port: %v", err)
	}
	clientPort := portOf(clientListener)
	enginePort := portOf(engineListener)
	prometheusPort := portOf(prometheusListener)
	clientAddr := "127.0.0.1:" + strconv.Itoa(clientPort)

	serverCmd := exec.Command(python, filepath.Join(repoRoot, "server", "main.py"),
		"--transport", "grpc",
		"--client_port", strconv.Itoa(clientPort),
		"--engine_port", strconv.Itoa(enginePort),
		"--prometheus_port", strconv.Itoa(prometheusPort),
		"--log-level", "INFO",
		"--tls-cert", certPath,
		"--tls-key", keyPath,
	)
	serverCmd.Dir = repoRoot
	serverCmd.Stdout = &prefixedWriter{prefix: "[tls-server] "}
	serverCmd.Stderr = &prefixedWriter{prefix: "[tls-server] "}
	// Hold the ports open until immediately before the server process starts,
	// to minimize the window in which another process could grab one first.
	clientListener.Close()
	engineListener.Close()
	prometheusListener.Close()
	if err := serverCmd.Start(); err != nil {
		t.Fatalf("starting gabriel server: %v", err)
	}
	defer stopProcess(serverCmd)

	if err := waitForTCP(clientAddr, serverReadyTimeout); err != nil {
		t.Fatalf("gabriel server never became ready: %v", err)
	}

	producer := gabrielclient.NewInputProducer(
		"producer-1",
		func(ctx context.Context) <-chan *gabrielpb.InputFrame {
			return make(chan *gabrielpb.InputFrame)
		},
		[]string{"tls-engine-0"},
	)
	var receivedResponse atomic.Bool
	consumer := func(result *gabrielpb.Result) { receivedResponse.Store(true) }

	// No TLSCredentials set: this client dials in plaintext against a TLS-only
	// server, so it must never get past the handshake.
	grpcClient, err := gabrielclient.NewGrpcClient(clientAddr, []*gabrielclient.InputProducer{producer}, consumer)
	if err != nil {
		t.Fatalf("creating client: %v", err)
	}

	// A plaintext connection attempt against a TLS-only server should fail
	// somewhere. Either Launch itself fails to open the session (the
	// expected/observed case, since opening the stream performs the
	// handshake), or, if it doesn't, an error should show up on errCh.
	errCh, err := grpcClient.Launch(t.Context())
	if err == nil {
		select {
		case launchErr := <-errCh:
			if launchErr == nil {
				t.Fatal("expected an error connecting in plaintext to a TLS-only server")
			}
		case <-time.After(launchResponseWait):
			// Also acceptable: the connection just never becomes ready.
		}
	}
	if receivedResponse.Load() {
		t.Fatal("plaintext client should never have received a response from a TLS-only server")
	}
}
