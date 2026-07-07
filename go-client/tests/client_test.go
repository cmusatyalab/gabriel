package test

import (
	"context"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	gabrielclient "github.com/cmusatyalab/gabriel/go-client"
	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
)

const (
	launchResponseWait     = 1 * time.Second
	targetEngineSwitchWait = 1 * time.Second
	inputInterval          = 50 * time.Millisecond
)

// repeatingProducer returns a Producer that emits a text frame with the given
// payload every interval until its context is canceled.
func repeatingProducer(payload string, interval time.Duration) gabrielclient.Producer {
	return func(ctx context.Context) <-chan *gabrielpb.InputFrame {
		ch := make(chan *gabrielpb.InputFrame)
		go func() {
			defer close(ch)
			ticker := time.NewTicker(interval)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					frame := &gabrielpb.InputFrame{
						Payload: &gabrielpb.InputFrame_StringPayload{
							StringPayload: payload,
						},
					}
					select {
					case ch <- frame:
					case <-ctx.Done():
						return
					}
				}
			}
		}()
		return ch
	}
}

// engineCounts is a thread-safe tally of results received per engine ID,
// mirroring the multiple-engine consumer used by the Python integration
// tests.
type engineCounts struct {
	mu     sync.Mutex
	counts map[string]int
}

func (c *engineCounts) consumer(result *gabrielpb.Result) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.counts == nil {
		c.counts = make(map[string]int)
	}
	c.counts[result.TargetEngineId]++
}

func (c *engineCounts) get(engineID string) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.counts[engineID]
}

func TestEndToEnd(t *testing.T) {
	producerFn := func(ctx context.Context) <-chan *gabrielpb.InputFrame {
		ch := make(chan *gabrielpb.InputFrame, 1)
		go func() {
			time.Sleep(inputInterval)
			frame := &gabrielpb.InputFrame{
				Payload: &gabrielpb.InputFrame_StringPayload{
					StringPayload: "Hello world!",
				},
			}
			ch <- frame
		}()
		return ch
	}
	producer := gabrielclient.NewInputProducer("producer-1", producerFn, []string{"engine-0"})
	var receivedResponse atomic.Bool
	consumer := func(result *gabrielpb.Result) {
		receivedResponse.Store(true)
	}
	grpcClient, _ := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, consumer)

	t.Log("Launching client")
	go grpcClient.Launch(t.Context())
	time.Sleep(launchResponseWait)
	if !receivedResponse.Load() {
		t.Fatal("Did not receive response")
	}
}

// TestMultipleEngines checks that a producer targeting several engines
// receives responses tagged with each of them.
func TestMultipleEngines(t *testing.T) {
	engine1 := startEngine(t)
	engine2 := startEngine(t)

	producer := gabrielclient.NewInputProducer(
		"producer-1", repeatingProducer("hi", inputInterval), []string{"engine-0", engine1, engine2},
	)
	counts := &engineCounts{}
	grpcClient, _ := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, counts.consumer)

	go grpcClient.Launch(t.Context())
	time.Sleep(launchResponseWait)

	for _, engineID := range []string{"engine-0", engine1, engine2} {
		if counts.get(engineID) == 0 {
			t.Errorf("did not receive a response from engine %s", engineID)
		}
	}
}

// TestInvalidEngineTarget checks that targeting an engine ID the server
// doesn't know about surfaces an error rather than silently hanging.
func TestInvalidEngineTarget(t *testing.T) {
	producer := gabrielclient.NewInputProducer(
		"producer-1", repeatingProducer("hi", inputInterval), []string{"nonexistent-engine"},
	)
	consumer := func(result *gabrielpb.Result) {}
	grpcClient, err := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, consumer)
	if err != nil {
		t.Fatalf("creating grpc client: %v", err)
	}

	errCh, err := grpcClient.Launch(t.Context())
	if err != nil {
		t.Fatalf("launching client: %v", err)
	}

	select {
	case launchErr := <-errCh:
		if launchErr == nil || !strings.Contains(launchErr.Error(), "not connected to the server") {
			t.Fatalf("unexpected error from client: %v", launchErr)
		}
	case <-time.After(launchResponseWait):
		t.Fatal("timed out waiting for error about invalid target engine")
	}
}

// TestEmptyInputFrame checks that a producer emitting an empty frame does
// not cause a response to be sent, nor bring the client down.
func TestEmptyInputFrame(t *testing.T) {
	emptyOnce := func(ctx context.Context) <-chan *gabrielpb.InputFrame {
		ch := make(chan *gabrielpb.InputFrame, 1)
		go func() {
			time.Sleep(inputInterval)
			ch <- &gabrielpb.InputFrame{}
		}()
		return ch
	}
	producer := gabrielclient.NewInputProducer("producer-1", emptyOnce, []string{"engine-0"})
	var receivedResponse atomic.Bool
	consumer := func(result *gabrielpb.Result) {
		receivedResponse.Store(true)
	}
	grpcClient, _ := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, consumer)

	go grpcClient.Launch(t.Context())
	time.Sleep(launchResponseWait)

	if receivedResponse.Load() {
		t.Fatal("expected no response for an empty input frame")
	}
}

// TestChangeTargetEngines checks that changing a producer's target engines
// on the fly redirects subsequent frames to the new targets.
func TestChangeTargetEngines(t *testing.T) {
	engine1 := startEngine(t)

	producer := gabrielclient.NewInputProducer(
		"producer-1", repeatingProducer("hi", inputInterval), []string{"engine-0"},
	)
	counts := &engineCounts{}
	grpcClient, _ := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, counts.consumer)

	go grpcClient.Launch(t.Context())
	time.Sleep(launchResponseWait)

	if counts.get("engine-0") == 0 {
		t.Fatal("did not receive a response from engine 0 before changing targets")
	}

	producer.ChangeTargetEngines([]string{engine1})
	time.Sleep(targetEngineSwitchWait)

	if counts.get(engine1) == 0 {
		t.Fatalf("did not receive a response from engine %s after changing targets", engine1)
	}
}

// TestAddRemoveTargetEngine checks that AddTargetEngine and
// RemoveTargetEngine incrementally adjust which engines a producer targets.
func TestAddRemoveTargetEngine(t *testing.T) {
	engine1 := startEngine(t)

	producer := gabrielclient.NewInputProducer(
		"producer-1", repeatingProducer("hi", inputInterval), []string{"engine-0"},
	)
	counts := &engineCounts{}
	grpcClient, _ := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, counts.consumer)

	go grpcClient.Launch(t.Context())
	time.Sleep(launchResponseWait)

	if counts.get("engine-0") == 0 {
		t.Fatal("did not receive a response from engine 0")
	}

	producer.AddTargetEngine(engine1)
	time.Sleep(targetEngineSwitchWait)

	if counts.get(engine1) == 0 {
		t.Fatalf("did not receive a response from engine %s after adding it as a target", engine1)
	}

	producer.RemoveTargetEngine("engine-0")
	countAfterRemoval := counts.get("engine-0")
	time.Sleep(targetEngineSwitchWait)

	// Allow a small tolerance for a frame that was already in flight to engine
	// 0 at the moment it was removed as a target.
	if counts.get("engine-0")-countAfterRemoval > 1 {
		t.Fatalf(
			"still receiving responses from engine 0 after removing it as a target: %d -> %d",
			countAfterRemoval, counts.get("engine-0"),
		)
	}
}
