package gabrielclient

import (
	"context"
	"sync"
	"sync/atomic"

	gabrielpb "github.com/cmusatyalab/gabriel/protocol/go"
	"golang.org/x/sync/semaphore"
)

// ProducerFunc produces input frames on the returned channel until ctx is
// canceled. It is responsible for closing the channel when ctx is canceled.
type ProducerFunc func(ctx context.Context) <-chan *gabrielpb.InputFrame

// InputProducer wraps a ProducerFunc with a name, target engines, and
// pause/resume state.
type InputProducer struct {
	Name            string
	producer        ProducerFunc
	targetEngineIDs map[string]struct{}
	running         bool
	engineMu        sync.Mutex
	runningMu       sync.Mutex
	cond            *sync.Cond
}

// NewInputProducer creates a new input producer with the given name and
// producer function. The resulting InputProducer will target engines
// specified by targetEngineIDs.
func NewInputProducer(name string, producer ProducerFunc, targetEngineIDs []string) *InputProducer {
	inputProducer := &InputProducer{
		Name:            name,
		producer:        producer,
		targetEngineIDs: make(map[string]struct{}),
		running:         true,
	}
	for _, engineID := range targetEngineIDs {
		inputProducer.targetEngineIDs[engineID] = struct{}{}
	}
	inputProducer.cond = sync.NewCond(&inputProducer.runningMu)
	return inputProducer
}

// Produce calls the underlying producer function to produce input frames. The
// returned channel will be closed when the given context is canceled.
func (p *InputProducer) Produce(ctx context.Context) <-chan *gabrielpb.InputFrame {
	return p.producer(ctx)
}

// Pause pauses the production of input frames.
func (p *InputProducer) Pause() {
	p.runningMu.Lock()
	defer p.runningMu.Unlock()
	p.running = false
}

// Resume resumes the production of input frames.
func (p *InputProducer) Resume() {
	p.runningMu.Lock()
	defer p.runningMu.Unlock()
	p.running = true
	p.cond.Signal()
}

// ChangeTargetEngines changes the target engines of the InputProducer to the
// given targetEngineIDs.
func (p *InputProducer) ChangeTargetEngines(targetEngineIDs []string) {
	p.engineMu.Lock()
	defer p.engineMu.Unlock()
	p.targetEngineIDs = make(map[string]struct{})
	for _, engineID := range targetEngineIDs {
		p.targetEngineIDs[engineID] = struct{}{}
	}
}

// AddTargetEngine adds the given engineID to the target engines of the
// InputProducer.
func (p *InputProducer) AddTargetEngine(engineID string) {
	p.engineMu.Lock()
	defer p.engineMu.Unlock()
	p.targetEngineIDs[engineID] = struct{}{}
}

// RemoveTargetEngine removes the given engineID from the target engines of
// the InputProducer.
func (p *InputProducer) RemoveTargetEngine(engineID string) {
	p.engineMu.Lock()
	defer p.engineMu.Unlock()
	delete(p.targetEngineIDs, engineID)
}

// IsRunning returns true if the InputProducer is running.
func (p *InputProducer) IsRunning() bool {
	p.runningMu.Lock()
	defer p.runningMu.Unlock()
	return p.running
}

// waitForRunning blocks until the InputProducer is running.
func (p *InputProducer) waitForRunning() {
	p.runningMu.Lock()
	defer p.runningMu.Unlock()
	for !p.running {
		p.cond.Wait()
	}
}

// TargetEngineIDs returns the target engine IDs of the InputProducer.
func (p *InputProducer) TargetEngineIDs() []string {
	p.engineMu.Lock()
	defer p.engineMu.Unlock()
	engineIDs := make([]string, 0, len(p.targetEngineIDs))
	for engineID := range p.targetEngineIDs {
		engineIDs = append(engineIDs, engineID)
	}
	return engineIDs
}

// tokenPool manages the tokens for a single InputProducer. remaining mirrors
// the semaphore's available weight for the gabriel_producer_token_count
// metric, since semaphore.Weighted exposes no way to query it directly.
type tokenPool struct {
	sem          *semaphore.Weighted
	maxTokens    int
	producerName string
	remaining    atomic.Int64
}

// newTokenPool creates a tokenPool with maxTokens available, and initializes
// its gabriel_producer_token_count gauge.
func newTokenPool(maxTokens int, producerName string) *tokenPool {
	pool := &tokenPool{
		sem:          semaphore.NewWeighted(int64(maxTokens)),
		maxTokens:    maxTokens,
		producerName: producerName,
	}
	pool.remaining.Store(int64(maxTokens))
	producerTokenCount.WithLabelValues(producerName).Set(float64(maxTokens))
	return pool
}

func (pool *tokenPool) ResetTokens() {
	pool.sem = semaphore.NewWeighted(int64(pool.maxTokens))
	pool.remaining.Store(int64(pool.maxTokens))
	producerTokenCount.WithLabelValues(pool.producerName).
		Set(float64(pool.maxTokens))
}

func (pool *tokenPool) GetToken(ctx context.Context) error {
	if err := pool.sem.Acquire(ctx, 1); err != nil {
		return err
	}
	remaining := pool.remaining.Add(-1)
	producerTokenCount.WithLabelValues(pool.producerName).
		Set(float64(remaining))
	return nil
}

func (pool *tokenPool) ReturnToken() {
	pool.sem.Release(1)
	remaining := pool.remaining.Add(1)
	producerTokenCount.WithLabelValues(pool.producerName).
		Set(float64(remaining))
}
