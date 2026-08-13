package gabrielclient

import (
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Metrics registered against the default Prometheus registerer, so they are
// served by promhttp.Handler() when WithPrometheusPort is used.
var (
	producerTokenCount = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "gabriel_producer_token_count",
		Help: "Number of tokens remaining at each producer",
	}, []string{"producer_id"})

	producerInputsSentTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "gabriel_producer_inputs_sent_total",
		Help: "Total number of client inputs sent from a producer",
	}, []string{"producer_id"})

	clientInputProcessingLatency = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name: "gabriel_client_input_processing_latency_seconds",
		Help: "End-to-end client input processing latency",
	}, []string{"producer_id"})
)

// pendingResultKey identifies an in-flight input awaiting a result, so its
// send time can be recovered when the result arrives. frame_id alone isn't
// unique: each producer numbers its own frames starting at 1, so two
// producers can be in flight with the same frame_id at once.
type pendingResultKey struct {
	producerID string
	frameID    int64
}

// pendingResults tracks the send time of every in-flight input, keyed by
// producer and frame id, so recordResponseLatency can compute the round-trip
// time once a result comes back. Guarded by pendingResultsMu since producer
// streams (writers) and the control stream (reader) run on separate
// goroutines.
type pendingResults struct {
	mu      sync.Mutex
	sendLog map[pendingResultKey]time.Time
}

func newPendingResults() *pendingResults {
	return &pendingResults{sendLog: make(map[pendingResultKey]time.Time)}
}

// recordSend records metrics for an input just handed off to the server. It
// increments the sent counter and stashes the send time for latency tracking
// once its result arrives.
func (p *pendingResults) recordSend(producerID string, frameID int64) {
	producerInputsSentTotal.WithLabelValues(producerID).Inc()
	p.mu.Lock()
	p.sendLog[pendingResultKey{producerID, frameID}] = time.Now()
	p.mu.Unlock()
}

// recordResponse looks up the send time for (producerID, frameID) and, if
// found, observes the elapsed time on the latency histogram and forgets it.
func (p *pendingResults) recordResponse(producerID string, frameID int64) {
	key := pendingResultKey{producerID, frameID}
	p.mu.Lock()
	sendTime, ok := p.sendLog[key]
	if ok {
		delete(p.sendLog, key)
	}
	p.mu.Unlock()
	if !ok {
		return
	}
	clientInputProcessingLatency.WithLabelValues(producerID).
		Observe(time.Since(sendTime).Seconds())
}
