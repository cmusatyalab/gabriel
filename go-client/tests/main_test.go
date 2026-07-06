package test

import (
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

// grpcServerAddr is the address of the Gabriel gRPC server started by
// TestMain, available for the duration of this package's tests.
var grpcServerAddr string

// python and repoRoot are set once by TestMain and used by startEngine to
// launch additional echo engine subprocesses on demand from individual tests.
var (
	python       string
	repoRoot     string
	engineScript string
)

const (
	testEngineID       = "0"
	engineBackendAddr  = "tcp://localhost:5555"
	serverReadyTimeout = 2 * time.Second
	defaultPyenvEnv    = "gabriel-ci"
	engineRegisterWait = time.Second
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
	repoRoot = filepath.Join(wd, "..", "..")
	serverMain := filepath.Join(repoRoot, "server", "main.py")
	engineScript = filepath.Join(wd, "testdata", "echo_engine.py")

	python = resolvePythonInterpreter()

	port, err := freeTCPPort()
	if err != nil {
		return 1, fmt.Errorf("finding a free port: %w", err)
	}
	grpcServerAddr = fmt.Sprintf("127.0.0.1:%d", port)

	serverCmd := exec.Command(python, serverMain,
		"--transport", "grpc",
		"--port", strconv.Itoa(port),
	)
	serverCmd.Dir = repoRoot
	serverCmd.Stdout = &prefixedWriter{prefix: "[server] "}
	serverCmd.Stderr = &prefixedWriter{prefix: "[server] "}
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
	// The gRPC port comes up before the engine has finished its ZeroMQ
	// handshake with the server; give it a moment to register.
	time.Sleep(engineRegisterWait)

	return m.Run(), nil
}

// newEngineCmd builds (but does not start) an echo engine subprocess with
// the given engine ID, connected to the shared engine backend.
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
	// Give the engine time to complete its ZeroMQ handshake with the server
	// and for the server to notify the (already-connected) gRPC clients.
	time.Sleep(engineRegisterWait)
	return engineID
}

// resolvePythonInterpreter finds the Python interpreter to run the Gabriel
// server and test engine with. It prefers, in order: an explicit
// GABRIEL_TEST_PYTHON override, the pyenv environment named by
// GABRIEL_TEST_PYENV_VERSION (or "gabriel-ci" by default), and finally falls
// back to whatever "python3" is on PATH (e.g. in CI, where dependencies are
// installed directly rather than via pyenv).
func resolvePythonInterpreter() string {
	if p := os.Getenv("GABRIEL_TEST_PYTHON"); p != "" {
		return p
	}

	pyenvEnv := os.Getenv("GABRIEL_TEST_PYENV_VERSION")
	if pyenvEnv == "" {
		pyenvEnv = defaultPyenvEnv
	}
	cmd := exec.Command("pyenv", "which", "python")
	cmd.Env = append(os.Environ(), "PYENV_VERSION="+pyenvEnv)
	if out, err := cmd.Output(); err == nil {
		if p := strings.TrimSpace(string(out)); p != "" {
			return p
		}
	}

	return "python3"
}

func freeTCPPort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
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
