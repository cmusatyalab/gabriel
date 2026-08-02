package gabrielclient_test

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	gabrielclient "github.com/cmusatyalab/gabriel/go-client"
	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
)

const (
	// launchResponseWait/targetEngineSwitchWait are upper bounds on how long a
	// test polls for an expected condition. They're also used, unpolled, as
	// fixed waits for negative assertions (nothing should have happened) where
	// there's no condition to poll for.
	launchResponseWait     = 3 * time.Second
	targetEngineSwitchWait = 3 * time.Second
	inputInterval          = 50 * time.Millisecond
	pollInterval           = 50 * time.Millisecond
	// multipleEnginesWait bounds how long TestMultipleEngines polls for every
	// engine to have received at least one response. It's longer than
	// launchResponseWait to give scheduling/timing jitter under CI load some
	// headroom before a given engine gets its first frame.
	multipleEnginesWait = 10 * time.Second
	// inFlightDrainWait bounds how long frames that snapshotted a producer's
	// target engines before a change takes effect may take to be sent and
	// answered by the server.
	inFlightDrainWait = 10 * inputInterval
)

// waitUntil polls cond every interval until it returns true or timeout
// elapses, returning as soon as cond is satisfied. Returns whether cond became
// true within the timeout.
func waitUntil(timeout, interval time.Duration, cond func() bool) bool {
	deadline := time.Now().Add(timeout)
	for {
		if cond() {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(interval)
	}
}

// repeatingProducer returns a ProducerFunc that emits a text frame with the given
// payload every interval until its context is canceled.
func repeatingProducer(payload string, interval time.Duration) gabrielclient.ProducerFunc {
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
// mirroring the multiple-engine consumer used by the Python integration tests.
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
	useTestLogger(t)
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
	grpcClient, err := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, consumer)
	if err != nil {
		t.Fatalf("creating client: %v", err)
	}

	t.Log("Launching client")
	go grpcClient.Launch(t.Context())
	if !waitUntil(launchResponseWait, pollInterval, receivedResponse.Load) {
		t.Fatal("Did not receive response")
	}
}

// TestMultipleEngines checks that a producer targeting several engines
// receives responses tagged with each of them.
func TestMultipleEngines(t *testing.T) {
	useTestLogger(t)
	engine1 := startEngine(t)
	engine2 := startEngine(t)

	producer := gabrielclient.NewInputProducer(
		"producer-1", repeatingProducer("hi", inputInterval), []string{"engine-0", engine1, engine2},
	)
	counts := &engineCounts{}
	grpcClient, err := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, counts.consumer)
	if err != nil {
		t.Fatalf("creating client: %v", err)
	}

	go grpcClient.Launch(t.Context())

	for _, engineID := range []string{"engine-0", engine1, engine2} {
		engineID := engineID
		if !waitUntil(multipleEnginesWait, pollInterval, func() bool { return counts.get(engineID) > 0 }) {
			t.Errorf("did not receive a response from engine %s", engineID)
		}
	}
}

// TestInvalidEngineTarget checks that targeting an engine ID the server
// doesn't know about surfaces an error rather than silently hanging.
func TestInvalidEngineTarget(t *testing.T) {
	useTestLogger(t)
	producer := gabrielclient.NewInputProducer(
		"producer-1", repeatingProducer("hi", inputInterval), []string{"nonexistent-engine"},
	)
	consumer := func(result *gabrielpb.Result) {}
	grpcClient, err := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, consumer)
	if err != nil {
		t.Fatalf("creating client: %v", err)
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

// TestEmptyInputFrame checks that a producer emitting an empty frame does not
// cause a response to be sent, nor bring the client down.
func TestEmptyInputFrame(t *testing.T) {
	useTestLogger(t)
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
	grpcClient, err := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, consumer)
	if err != nil {
		t.Fatalf("creating client: %v", err)
	}

	go grpcClient.Launch(t.Context())
	// Negative assertion: wait the full window since there's no condition to
	// poll for, then confirm nothing arrived.
	time.Sleep(launchResponseWait)

	if receivedResponse.Load() {
		t.Fatal("expected no response for an empty input frame")
	}
}

// TestChangeTargetEngines checks that changing a producer's target engines on
// the fly redirects subsequent frames to the new targets.
func TestChangeTargetEngines(t *testing.T) {
	useTestLogger(t)
	engine1 := startEngine(t)

	producer := gabrielclient.NewInputProducer(
		"producer-1", repeatingProducer("hi", inputInterval), []string{"engine-0"},
	)
	counts := &engineCounts{}
	grpcClient, err := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, counts.consumer)
	if err != nil {
		t.Fatalf("creating client: %v", err)
	}

	go grpcClient.Launch(t.Context())

	if !waitUntil(launchResponseWait, pollInterval, func() bool { return counts.get("engine-0") > 0 }) {
		t.Fatal("did not receive a response from engine 0 before changing targets")
	}

	producer.ChangeTargetEngines([]string{engine1})

	if !waitUntil(targetEngineSwitchWait, pollInterval, func() bool { return counts.get(engine1) > 0 }) {
		t.Fatalf("did not receive a response from engine %s after changing targets", engine1)
	}
}

// TestAddRemoveTargetEngine checks that AddTargetEngine and RemoveTargetEngine
// incrementally adjust which engines a producer targets.
func TestAddRemoveTargetEngine(t *testing.T) {
	useTestLogger(t)
	engine1 := startEngine(t)

	producer := gabrielclient.NewInputProducer(
		"producer-1", repeatingProducer("hi", inputInterval), []string{"engine-0"},
	)
	counts := &engineCounts{}
	grpcClient, err := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, counts.consumer)
	if err != nil {
		t.Fatalf("creating client: %v", err)
	}

	go grpcClient.Launch(t.Context())

	if !waitUntil(launchResponseWait, pollInterval, func() bool { return counts.get("engine-0") > 0 }) {
		t.Fatal("did not receive a response from engine 0")
	}

	producer.AddTargetEngine(engine1)

	if !waitUntil(targetEngineSwitchWait, pollInterval, func() bool { return counts.get(engine1) > 0 }) {
		t.Fatalf("did not receive a response from engine %s after adding it as a target", engine1)
	}

	producer.RemoveTargetEngine("engine-0")
	// The producer's token pool allows multiple frames to already have
	// snapshotted the old target list before removal takes effect, so give any
	// such in-flight frames time to be sent and answered before taking our
	// baseline count.
	time.Sleep(inFlightDrainWait)
	countAfterRemoval := counts.get("engine-0")

	// Negative assertion: wait the full window since there's no condition to
	// poll for, then confirm no further responses arrived from engine 0.
	time.Sleep(targetEngineSwitchWait)
	if counts.get("engine-0") != countAfterRemoval {
		t.Fatalf(
			"still receiving responses from engine 0 after removing it as a target: %d -> %d",
			countAfterRemoval, counts.get("engine-0"),
		)
	}
}

// ExampleNewGrpcClient demonstrates the minimal setup for sending a single
// input frame to a Gabriel server and receiving its result: an InputProducer
// producing frames, a consumer callback handling results, and a GrpcClient
// tying the two together.
func ExampleNewGrpcClient() {
	producerFn := func(ctx context.Context) <-chan *gabrielpb.InputFrame {
		ch := make(chan *gabrielpb.InputFrame, 1)
		ch <- &gabrielpb.InputFrame{
			Payload: &gabrielpb.InputFrame_StringPayload{
				StringPayload: "Hello world!",
			},
		}
		return ch
	}
	producer := gabrielclient.NewInputProducer("producer-1", producerFn, []string{"engine-0"})

	done := make(chan struct{})
	var once sync.Once
	consumer := func(result *gabrielpb.Result) {
		once.Do(func() {
			fmt.Println("received response")
			close(done)
		})
	}

	grpcClient, err := gabrielclient.NewGrpcClient(grpcServerAddr, []*gabrielclient.InputProducer{producer}, consumer)
	if err != nil {
		panic(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if _, err := grpcClient.Launch(ctx); err != nil {
		fmt.Println("error launching client:", err)
		return
	}

	select {
	case <-done:
	case <-time.After(launchResponseWait):
		fmt.Println("timed out waiting for response")
	}

	// Output:
	// received response
}
