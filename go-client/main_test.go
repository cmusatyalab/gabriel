package gabrielclient_test

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
)

// grpcServerAddr is the address of the Gabriel gRPC server started by
// TestMain, available for the duration of this package's tests.
var grpcServerAddr string

// python and repoRoot are set once by TestMain and used by startEngine to
// launch additional echo engine subprocesses on demand from individual tests.
var (
	python            string
	repoRoot          string
	engineScript      string
	engineBackendAddr string
)

const (
	testEngineID           = "engine-0"
	serverReadyTimeout     = 2 * time.Second
	engineRegisterTimeout  = 10 * time.Second
	engineRegisterPollWait = 100 * time.Millisecond
)

// TestMain spins up a real Gabriel gRPC server and a minimal echo engine as
// subprocesses so the tests in this package can run against a live server
// without requiring one to be started manually beforehand.
func TestMain(m *testing.M) {
	code, err := runWithGabrielServer(m)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
	os.Exit(code)
}

func runWithGabrielServer(m *testing.M) (int, error) {
	wd, err := os.Getwd()
	if err != nil {
		return 1, fmt.Errorf("getwd: %w", err)
	}
	repoRoot = filepath.Join(wd, "..")
	serverMain := filepath.Join(repoRoot, "server", "main.py")
	engineScript = filepath.Join(wd, "testdata", "echo_engine.py")

	python = resolvePythonInterpreter()

	grpcListener, err := reservePort()
	if err != nil {
		return 1, fmt.Errorf("finding a free port: %w", err)
	}
	grpc_port := portOf(grpcListener)
	grpcServerAddr = fmt.Sprintf("127.0.0.1:%d", grpc_port)
	fmt.Fprintf(os.Stderr, "Using port %d for gRPC server\n", grpc_port)

	engineListener, err := reservePort()
	if err != nil {
		return 1, fmt.Errorf("finding a free port: %w", err)
	}
	engine_port := portOf(engineListener)
	fmt.Fprintf(os.Stderr, "Using port %d for gabriel server\n", engine_port)
	serverCmd := exec.Command(python, serverMain,
		"--transport", "grpc",
		"--client_port", strconv.Itoa(grpc_port),
		"--log-level", "INFO",
		"--engine_port", strconv.Itoa(engine_port),
	)
	engineBackendAddr = fmt.Sprintf("127.0.0.1:%d", engine_port)
	serverCmd.Dir = repoRoot
	serverCmd.Stdout = &prefixedWriter{prefix: "[server] "}
	serverCmd.Stderr = &prefixedWriter{prefix: "[server] "}
	// Hold the ports open until immediately before the process that will
	// bind them actually starts, to minimize the window in which another
	// process could grab one first.
	grpcListener.Close()
	engineListener.Close()
	if err := serverCmd.Start(); err != nil {
		return 1, fmt.Errorf("starting gabriel server: %w", err)
	}
	defer stopProcess(serverCmd)

	engineCmd := newEngineCmd(testEngineID)
	if err := engineCmd.Start(); err != nil {
		return 1, fmt.Errorf("starting echo engine: %w", err)
	}
	defer stopProcess(engineCmd)

	if err := waitForTCP(grpcServerAddr, serverReadyTimeout); err != nil {
		return 1, fmt.Errorf("gabriel server never became ready: %w", err)
	}
	// The client gRPC port comes up before the engine has finished its gRPC
	// handshake with the server; actively poll until it's actually registered
	// rather than guessing how long that takes.
	if err := waitForEngineRegistered(grpcServerAddr, testEngineID, nil, engineRegisterTimeout); err != nil {
		return 1, fmt.Errorf("engine %s never registered: %w", testEngineID, err)
	}

	return m.Run(), nil
}

// newEngineCmd builds (but does not start) an echo engine subprocess with the
// given engine ID, connected to the shared engine backend.
func newEngineCmd(engineID string) *exec.Cmd {
	cmd := exec.Command(python, engineScript,
		"--engine-id", engineID,
		"--server-address", engineBackendAddr,
	)
	cmd.Dir = repoRoot
	prefix := fmt.Sprintf("[engine-%s] ", engineID)
	cmd.Stdout = &prefixedWriter{prefix: prefix}
	cmd.Stderr = &prefixedWriter{prefix: prefix}
	return cmd
}

// engineIDCounter hands out engine IDs for startEngine that are unique for the
// lifetime of the test binary, so a repeated `go test -count=N` run never
// reconnects an engine under an ID it only just disconnected under.
var engineIDCounter atomic.Int64

// startEngine launches an additional echo engine subprocess under a freshly
// generated engine ID, connected to the shared Gabriel server started by
// TestMain, and returns that ID. The engine is stopped automatically via
// t.Cleanup when the calling test completes.
func startEngine(t *testing.T) string {
	t.Helper()
	engineID := fmt.Sprintf("engine-%d", engineIDCounter.Add(1))
	cmd := newEngineCmd(engineID)
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting echo engine %s: %v", engineID, err)
	}
	t.Cleanup(func() { stopProcess(cmd) })
	// Actively poll until the engine has completed its gRPC handshake with
	// the server, rather than guessing how long that takes.
	if err := waitForEngineRegistered(grpcServerAddr, engineID, nil, engineRegisterTimeout); err != nil {
		t.Fatalf("engine %s never registered: %v", engineID, err)
	}
	return engineID
}

// waitForEngineRegistered polls the server (by opening a throwaway
// ClientSession and inspecting the Registered message's engine list) until
// engineID is reported as connected, or timeout elapses. creds may be nil to
// dial in plaintext (insecure).
func waitForEngineRegistered(serverAddr, engineID string, creds credentials.TransportCredentials, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	var lastErr error
	for time.Now().Before(deadline) {
		registered, err := probeEngineRegistered(serverAddr, engineID, creds)
		if err != nil {
			lastErr = err
		} else if registered {
			return nil
		}
		time.Sleep(engineRegisterPollWait)
	}
	return fmt.Errorf("timed out after %s (last error: %v)", timeout, lastErr)
}

// probeEngineRegistered opens a short-lived ClientSession to the server,
// sends a Registration message, and reports whether the resulting Registered
// message lists engineID as connected. creds may be nil to dial in plaintext
// (insecure).
func probeEngineRegistered(serverAddr, engineID string, creds credentials.TransportCredentials) (bool, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	if creds == nil {
		creds = insecure.NewCredentials()
	}
	conn, err := grpc.NewClient(serverAddr, grpc.WithTransportCredentials(creds))
	if err != nil {
		return false, fmt.Errorf("dialing server: %w", err)
	}
	defer conn.Close()

	stream, err := gabrielpb.NewGabrielClientServiceClient(conn).ClientSession(ctx)
	if err != nil {
		return false, fmt.Errorf("opening probe session: %w", err)
	}

	if err := stream.Send(&gabrielpb.FromClient{
		MessageType: &gabrielpb.FromClient_Registration_{
			Registration: &gabrielpb.FromClient_Registration{},
		},
	}); err != nil {
		return false, fmt.Errorf("sending registration: %w", err)
	}

	msg, err := stream.Recv()
	if err != nil {
		return false, fmt.Errorf("receiving registered: %w", err)
	}
	registered := msg.GetRegistered()
	if registered == nil {
		return false, fmt.Errorf("first message from server was not a registered ack")
	}
	return slices.Contains(registered.EngineIds, engineID), nil
}

// resolvePythonInterpreter finds the Python interpreter to run the Gabriel
// server and test engine with. It prefers, in order: an explicit
// GABRIEL_TEST_PYTHON override, the pyenv environment named by
// GABRIEL_TEST_PYENV_VERSION, and finally falls back to whatever "python3" is
// on PATH (e.g. in CI, where dependencies are installed directly rather than
// via pyenv).
func resolvePythonInterpreter() string {
	if p := os.Getenv("GABRIEL_TEST_PYTHON"); p != "" {
		return p
	}

	if pyenvEnv := os.Getenv("GABRIEL_TEST_PYENV_VERSION"); pyenvEnv != "" {
		cmd := exec.Command("pyenv", "which", "python")
		cmd.Env = append(os.Environ(), "PYENV_VERSION="+pyenvEnv)
		if out, err := cmd.Output(); err == nil {
			if p := strings.TrimSpace(string(out)); p != "" {
				return p
			}
		}
	}

	return "python3"
}

// reservePort asks the OS for a free TCP port and holds it open by keeping the
// returned listener listening. A port handed out by closing the listener
// immediately after asking for it would be subject to a race: another process
// can grab the same port before the intended subprocess gets around to binding
// it.  Callers of reservePort should keep the listener open for as long as
// possible and Close it immediately before starting the process that needs the
// port, to shrink that window as much as possible.
func reservePort() (*net.TCPListener, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	return l.(*net.TCPListener), nil
}

// portOf returns the port number a listener from reservePort is bound to.
func portOf(l *net.TCPListener) int {
	return l.Addr().(*net.TCPAddr).Port
}

func waitForTCP(addr string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	var lastErr error
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp", addr, 200*time.Millisecond)
		if err == nil {
			conn.Close()
			return nil
		}
		lastErr = err
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("timed out waiting for %s: %w", addr, lastErr)
}

// stopProcess asks a subprocess to terminate gracefully, escalating to SIGKILL
// if it doesn't exit within a few seconds. SIGTERM (rather than SIGINT) is
// used because Python has no default handler for it, so the process just exits
// instead of raising a noisy KeyboardInterrupt through asyncio.run().
func stopProcess(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	_ = cmd.Process.Signal(syscall.SIGTERM)

	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		_ = cmd.Process.Kill()
		<-done
	}
}

// useTestLogger redirects the go-client package's global zerolog logger (used
// by gabrielclient.Client) to t.Log for the duration of the calling test, so
// its output only surfaces when that test fails or -v is passed, matching go
// test's usual behavior for t.Log. Tests in this package run serially (none
// call t.Parallel), so swapping the process-wide logger around each test is
// safe.
func useTestLogger(t *testing.T) {
	t.Helper()
	prev := log.Logger
	w := &testLogWriter{t: t}
	log.Logger = zerolog.New(w).With().Timestamp().Logger()
	t.Cleanup(func() {
		w.done.Store(true)
		log.Logger = prev
	})
}

// testLogWriter adapts a *testing.T into an io.Writer for zerolog, stripping
// the trailing newline zerolog appends since t.Log adds its own. Client code
// under test (e.g. gabrielclient.Client.Launch) runs in background goroutines
// that aren't guaranteed to have exited by the time the test function returns,
// so a log call can race past the test's completion; calling t.Log at that
// point panics. done guards against that by falling back to stderr once the
// test has finished.
type testLogWriter struct {
	t    *testing.T
	done atomic.Bool
}

func (w *testLogWriter) Write(p []byte) (int, error) {
	line := strings.TrimRight(string(p), "\n")
	if w.done.Load() {
		fmt.Fprintln(os.Stderr, "[late log after "+w.t.Name()+" completed] "+line)
		return len(p), nil
	}
	w.t.Log(line)
	return len(p), nil
}

// prefixedWriter prefixes every line written to it and forwards it to
// os.Stderr, so subprocess output is distinguishable and only shown alongside
// test output (go test buffers/shows it on failure).
type prefixedWriter struct {
	prefix string
}

func (w *prefixedWriter) Write(p []byte) (int, error) {
	for line := range strings.SplitSeq(strings.TrimRight(string(p), "\n"), "\n") {
		if line == "" {
			continue
		}
		fmt.Fprintln(os.Stderr, w.prefix+line)
	}
	return len(p), nil
}
