package gabriel

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const (
	maxInputSize  = 64
	maxOutputSize = 64
	flowName      = "flow"
	// wait is far longer than anything here takes, so a test fails rather than hangs.
	wait = 5 * time.Second
)

var names atomic.Uint64

// unique returns a name no other test uses, in this process or another.
func unique(base string) string {
	return fmt.Sprintf("%s-%d-%d", base, os.Getpid(), names.Add(1))
}

// chain is a flow where producer feeds engine.
func chain(producer, engine string) string {
	return fmt.Sprintf(`
[[producers]]
name = %q
tokens = 1

[[engines]]
name = %q
location = "local"

[[edges]]
from = %q
to = %q
timeout = "5s"
`, producer, engine, producer, engine)
}

// fixture is a local server serving one engine, a client of it, and a producer the client takes
// inputs from. It must be used in the goroutine that made it.
type fixture struct {
	server   *Server
	client   *Client
	producer *Producer
	engine   *Engine
	names    struct{ producer, engine string }
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	f := &fixture{}
	f.names.producer, f.names.engine = unique("camera"), unique("detector")
	serverName := unique("server")
	var err error
	if f.server, err = NewServer(serverName); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.server.Close() })
	if f.engine, err = NewEngine(f.names.engine, "", maxOutputSize); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.engine.Close() })
	if err := f.server.AddEngine(f.names.engine); err != nil {
		t.Fatal(err)
	}
	if f.client, err = NewClient(unique("client"), serverName); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.client.Close() })
	if f.producer, err = NewProducer(f.names.producer, maxInputSize); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.producer.Close() })
	if err := f.client.AddProducer(f.names.producer); err != nil {
		t.Fatal(err)
	}
	return f
}

func timeout(t *testing.T) context.Context {
	ctx, cancel := context.WithTimeout(context.Background(), wait)
	t.Cleanup(cancel)
	return ctx
}

func TestInputReachesEngineAndResultReachesApp(t *testing.T) {
	f := newFixture(t)
	events, err := SubscribeProducer(f.names.producer)
	if err != nil {
		t.Fatal(err)
	}
	defer events.Close()
	if err := f.client.AddFlow(flowName, chain(f.names.producer, f.names.engine)); err != nil {
		t.Fatal(err)
	}

	seq, err := f.producer.Send(flowName, []byte("frame"))
	if err != nil {
		t.Fatal(err)
	}
	input, err := f.engine.Next(timeout(t))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(input.Data, []byte("frame")) {
		t.Errorf("engine got %q, want %q", input.Data, "frame")
	}
	if input.ID.Producer != f.names.producer || input.ID.Seq != seq {
		t.Errorf("engine got input %+v, want seq %d of %s", input.ID, seq, f.names.producer)
	}
	if err := f.engine.Pass([]byte("cat")); err != nil {
		t.Fatal(err)
	}

	for {
		event, err := events.Next(timeout(t))
		if err != nil {
			t.Fatal(err)
		}
		if event.Kind != EventResult {
			continue
		}
		if event.Engine != f.names.engine || event.ID.Seq != seq || event.Forward != ForwardPass {
			t.Errorf("result %+v, want a pass of seq %d by %s", event, seq, f.names.engine)
		}
		if !bytes.Equal(event.Result, []byte("cat")) {
			t.Errorf("result %q, want %q", event.Result, "cat")
		}
		if event.At.IsZero() || Now() < event.At {
			t.Errorf("result at %v, want a time before now (%v)", event.At, Now())
		}
		return
	}
}

func TestLoanedInputIsPublishedInPlace(t *testing.T) {
	f := newFixture(t)
	if err := f.client.AddFlow(flowName, chain(f.names.producer, f.names.engine)); err != nil {
		t.Fatal(err)
	}

	buf, err := f.producer.Loan()
	if err != nil {
		t.Fatal(err)
	}
	if len(buf) != maxInputSize {
		t.Fatalf("loaned %d bytes, want %d", len(buf), maxInputSize)
	}
	n := copy(buf, "in place")
	if _, err := f.producer.Publish(flowName, n); err != nil {
		t.Fatal(err)
	}

	input, err := f.engine.Next(timeout(t))
	if err != nil {
		t.Fatal(err)
	}
	if string(input.Data) != "in place" {
		t.Errorf("engine got %q, want %q", input.Data, "in place")
	}
}

func TestErrorSaysWhatFailed(t *testing.T) {
	f := newFixture(t)

	err := f.client.AddFlow(flowName, "[[edges]")

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("got %v, want ErrInvalid", err)
	}
	if !strings.HasPrefix(err.Error(), "gabriel: invalid argument: ") {
		t.Errorf("got message %q, want one that says what was invalid", err)
	}
}

func TestOversizedInputIsRefused(t *testing.T) {
	f := newFixture(t)

	_, err := f.producer.Send(flowName, make([]byte, maxInputSize+1))

	if !errors.Is(err, ErrSizeExceeded) {
		t.Fatalf("got %v, want ErrSizeExceeded", err)
	}
}

func TestEngineWithoutInputTimesOut(t *testing.T) {
	f := newFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()

	_, err := f.engine.Next(ctx)

	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("got %v, want the context's deadline", err)
	}
}

func TestEngineUsedFromAnotherGoroutineFails(t *testing.T) {
	f := newFixture(t)

	errs := make(chan error)
	go func() {
		_, err := f.engine.Poll()
		errs <- err
	}()

	if err := <-errs; !errors.Is(err, ErrWrongThread) {
		t.Fatalf("got %v, want ErrWrongThread", err)
	}
}

func TestClosedHandleFails(t *testing.T) {
	producer, err := NewProducer(unique("camera"), maxInputSize)
	if err != nil {
		t.Fatal(err)
	}
	if err := producer.Close(); err != nil {
		t.Fatal(err)
	}

	if _, err := producer.Loan(); !errors.Is(err, ErrClosed) {
		t.Fatalf("got %v, want ErrClosed", err)
	}
}

func TestRemoteServerGivesItsPortAndCertificate(t *testing.T) {
	server, err := NewServer(unique("server"), WithBindAddr("127.0.0.1:0"))
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	port, err := server.RemotePort()
	if err != nil || port == 0 {
		t.Errorf("got port %d, %v; want a port", port, err)
	}
	certificate, err := server.Certificate()
	if err != nil || len(certificate) == 0 {
		t.Errorf("got %d certificate bytes, %v; want a certificate", len(certificate), err)
	}
}

func TestClientTakesARemoteServerAndACrossingCapacity(t *testing.T) {
	server, err := NewServer(unique("server"), WithBindAddr("127.0.0.1:0"))
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	port, err := server.RemotePort()
	if err != nil {
		t.Fatal(err)
	}
	certificate, err := server.Certificate()
	if err != nil {
		t.Fatal(err)
	}
	addr := fmt.Sprintf("127.0.0.1:%d", port)

	client, err := NewClient(unique("client"), unique("local"), WithRemoteServer(addr, certificate), WithCrossingCapacity(4))

	if err != nil {
		t.Fatal(err)
	}
	client.Close()
}

func TestNegativeCrossingCapacityIsInvalid(t *testing.T) {
	_, err := NewClient(unique("client"), unique("local"), WithRemoteServer("127.0.0.1:4433", nil), WithCrossingCapacity(-1))

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("got %v, want ErrInvalid", err)
	}
}

func TestIdentityDirWithoutBindAddrIsInvalid(t *testing.T) {
	_, err := NewServer(unique("server"), WithIdentityDir(t.TempDir()))

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("got %v, want ErrInvalid", err)
	}
}

func TestCrossingCapacityNeedsARemoteServer(t *testing.T) {
	_, err := NewClient(unique("client"), unique("local"), WithCrossingCapacity(4))

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("got %v, want ErrInvalid", err)
	}
}

func TestEmittedInputReachesTheNextEngine(t *testing.T) {
	f := newFixture(t)
	trackerName := unique("tracker")
	tracker, err := NewEngine(trackerName, "", maxOutputSize)
	if err != nil {
		t.Fatal(err)
	}
	defer tracker.Close()
	if err := f.server.AddEngine(trackerName); err != nil {
		t.Fatal(err)
	}
	flow := chain(f.names.producer, f.names.engine) + fmt.Sprintf(`
[[engines]]
name = %q
location = "local"

[[edges]]
from = %q
to = %q
timeout = "5s"
`, trackerName, f.names.engine, trackerName)
	if err := f.client.AddFlow(flowName, flow); err != nil {
		t.Fatal(err)
	}

	if _, err := f.producer.Send(flowName, []byte("frame")); err != nil {
		t.Fatal(err)
	}
	if _, err := f.engine.Next(timeout(t)); err != nil {
		t.Fatal(err)
	}
	output, err := f.engine.Loan()
	if err != nil {
		t.Fatal(err)
	}
	n := copy(output, "tiles")
	if err := f.engine.Emit("example.Tiles", n, nil); err != nil {
		t.Fatal(err)
	}

	input, err := tracker.Next(timeout(t))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(input.Data, []byte("tiles")) || input.PayloadType != "example.Tiles" {
		t.Errorf("tracker got %q of type %q, want %q of type %q", input.Data, input.PayloadType, "tiles", "example.Tiles")
	}
}
