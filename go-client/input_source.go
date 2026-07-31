package gabrielclient

import (
	"context"
	"sync"

	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
	"golang.org/x/sync/semaphore"
)

// Producer produces input frames on the returned channel until ctx is
// canceled. It is responsible for closing the channel when ctx is canceled.
type Producer func(ctx context.Context) <-chan *gabrielpb.InputFrame

// InputSource wraps a Producer with a name, target engines, and pause/resume
// state.
type InputSource struct {
	Name            string
	producer        Producer
	targetEngineIDs map[string]struct{}
	running         bool
	engineMu        sync.Mutex
	runningMu       sync.Mutex
	cond            *sync.Cond
}

// NewInputSource creates a new input source with the given name and
// producer. The resulting InputSource will target engines specified by
// targetEngineIDs.
func NewInputSource(name string, producer Producer, targetEngineIDs []string) *InputSource {
	inputSource := &InputSource{
		Name:            name,
		producer:        producer,
		targetEngineIDs: make(map[string]struct{}),
		running:         true,
	}
	for _, engineID := range targetEngineIDs {
		inputSource.targetEngineIDs[engineID] = struct{}{}
	}
	inputSource.cond = sync.NewCond(&inputSource.runningMu)
	return inputSource
}

// Produce calls the underlying producer function to produce input frames. The
// returned channel will be closed when the given context is canceled.
func (producer *InputSource) Produce(ctx context.Context) <-chan *gabrielpb.InputFrame {
	return producer.producer(ctx)
}

// Pause pauses the production of input frames.
func (producer *InputSource) Pause() {
	producer.runningMu.Lock()
	defer producer.runningMu.Unlock()
	producer.running = false
}

// Resume resumes the production of input frames.
func (producer *InputSource) Resume() {
	producer.runningMu.Lock()
	defer producer.runningMu.Unlock()
	producer.running = true
	producer.cond.Signal()
}

// ChangeTargetEngines changes the target engines of the InputSource to the
// given targetEngineIDs.
func (producer *InputSource) ChangeTargetEngines(targetEngineIDs []string) {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	producer.targetEngineIDs = make(map[string]struct{})
	for _, engineID := range targetEngineIDs {
		producer.targetEngineIDs[engineID] = struct{}{}
	}
}

// AddTargetEngine adds the given engineID to the target engines of the
// InputSource.
func (producer *InputSource) AddTargetEngine(engineID string) {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	producer.targetEngineIDs[engineID] = struct{}{}
}

// RemoveTargetEngine removes the given engineID from the target engines of the
// InputSource.
func (producer *InputSource) RemoveTargetEngine(engineID string) {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	delete(producer.targetEngineIDs, engineID)
}

// IsRunning returns true if the InputSource is running.
func (producer *InputSource) IsRunning() bool {
	producer.runningMu.Lock()
	defer producer.runningMu.Unlock()
	return producer.running
}

// waitForRunning blocks until the InputSource is running.
func (producer *InputSource) waitForRunning() {
	producer.runningMu.Lock()
	defer producer.runningMu.Unlock()
	for !producer.running {
		producer.cond.Wait()
	}
}

// TargetEngineIDs returns the target engine IDs of the InputSource.
func (producer *InputSource) TargetEngineIDs() []string {
	producer.engineMu.Lock()
	defer producer.engineMu.Unlock()
	engineIDs := make([]string, 0, len(producer.targetEngineIDs))
	for engineID := range producer.targetEngineIDs {
		engineIDs = append(engineIDs, engineID)
	}
	return engineIDs
}

// tokenPool manages the tokens for a single InputSource.
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
