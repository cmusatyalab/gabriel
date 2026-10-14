package gabriel

// #include <stdlib.h>
// #include <gabriel/gabriel.h>
import "C"

import (
	"context"
	"fmt"
	"time"
	"unsafe"
)

// EventKind says what happened to an input.
type EventKind int

const (
	// EventAccepted: a server received the input and made one offer per target engine.
	EventAccepted EventKind = C.GABRIEL_EVENT_KIND_T_ACCEPTED
	// EventSent: the offer was sent to the engine; a result may come.
	EventSent EventKind = C.GABRIEL_EVENT_KIND_T_SENT
	// EventResult: the engine finished the input. Forward says what it passed on.
	EventResult EventKind = C.GABRIEL_EVENT_KIND_T_RESULT
	// EventSkipped: a newer input replaced the offer before it was sent.
	EventSkipped EventKind = C.GABRIEL_EVENT_KIND_T_SKIPPED
	// EventTimedOut: the engine did not answer in time, or was lost.
	EventTimedOut EventKind = C.GABRIEL_EVENT_KIND_T_TIMED_OUT
	// EventDisconnected: the client lost its connection to a server of the flow. Every offer on that
	// server ended with it. It names no input.
	EventDisconnected EventKind = C.GABRIEL_EVENT_KIND_T_DISCONNECTED
	// EventDerived: a result an engine derived from subscribed events. It answers no offer.
	EventDerived EventKind = C.GABRIEL_EVENT_KIND_T_DERIVED
	// EventLost: events were lost before the next one, because the subscriber fell behind.
	EventLost EventKind = C.GABRIEL_EVENT_KIND_T_LOST
)

func (k EventKind) String() string {
	switch k {
	case EventAccepted:
		return "accepted"
	case EventSent:
		return "sent"
	case EventResult:
		return "result"
	case EventSkipped:
		return "skipped"
	case EventTimedOut:
		return "timed out"
	case EventDisconnected:
		return "disconnected"
	case EventDerived:
		return "derived"
	case EventLost:
		return "lost"
	default:
		return fmt.Sprintf("EventKind(%d)", int(k))
	}
}

// Forward says what an engine forwarded along its outgoing edges.
type Forward int

const (
	// ForwardStop: nothing.
	ForwardStop Forward = C.GABRIEL_FORWARD_T_STOP
	// ForwardPass: the input, unchanged.
	ForwardPass Forward = C.GABRIEL_FORWARD_T_PASS
	// ForwardEmit: a new input.
	ForwardEmit Forward = C.GABRIEL_FORWARD_T_EMIT
)

func (f Forward) String() string {
	switch f {
	case ForwardStop:
		return "stop"
	case ForwardPass:
		return "pass"
	case ForwardEmit:
		return "emit"
	default:
		return fmt.Sprintf("Forward(%d)", int(f))
	}
}

// Event is what happened to one input at one engine. For [EventLost], only Lost is set; for
// [EventDisconnected], only Flow.
type Event struct {
	Kind EventKind
	// Lost is how many events were lost, for [EventLost].
	Lost uint64
	ID   InputID
	// Engine is the engine the event is about; empty if none.
	Engine string
	// Flow is the flow the event belongs to; empty if none.
	Flow string
	// Forward is what the engine forwarded, for [EventResult].
	Forward Forward
	// PayloadType says how to read Result; empty if there is no result.
	PayloadType string
	// Result is the engine's result, for [EventResult]. It is a copy, so it stays valid.
	Result []byte
	// At is when the event happened.
	At Timestamp
	// OfferedAt is when the server made the engine's offer; zero if the client made the offer, or
	// for [EventDisconnected].
	OfferedAt Timestamp
	// ReceivedAt is when the client received the event from the remote server; At and OfferedAt are
	// then on the remote server's clock. Zero for other events.
	ReceivedAt Timestamp
}

// Subscription reads the events about the inputs of one producer, or the events of one engine.
//
// A Subscription must be used and closed in the goroutine that created it (see Threads in the
// package documentation).
type Subscription struct {
	handle *C.gabriel_subscription_t
	thread thread
}

// SubscribeProducer subscribes to the events about the inputs of the producer name: what happened
// to each at each engine, and lost connections. Only events published from now on are read.
func SubscribeProducer(name string) (*Subscription, error) {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	t := lockThread()
	s := &Subscription{thread: t}
	err := call(func() C.gabriel_status_t { return C.gabriel_subscribe_producer(cName, &s.handle) })
	if err != nil {
		t.release()
		return nil, err
	}
	return s, nil
}

// SubscribeEngine subscribes to the events of the engine name: what happened to each input at
// that engine. Only events published from now on are read.
func SubscribeEngine(name string) (*Subscription, error) {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	t := lockThread()
	s := &Subscription{thread: t}
	err := call(func() C.gabriel_status_t { return C.gabriel_subscribe_engine(cName, &s.handle) })
	if err != nil {
		t.release()
		return nil, err
	}
	return s, nil
}

func (s *Subscription) check() error {
	if s.handle == nil {
		return failure(ErrClosed)
	}
	return s.thread.check()
}

// Wait waits until an event is ready, or timeout passes, and fails with [ErrAgain] then. The event
// itself comes from [Subscription.Poll].
func (s *Subscription) Wait(timeout time.Duration) error {
	if err := s.check(); err != nil {
		return err
	}
	return call(func() C.gabriel_status_t {
		return C.gabriel_subscription_wait(s.handle, milliseconds(timeout))
	})
}

// Poll returns the next event, or fails with [ErrAgain] if none is available.
func (s *Subscription) Poll() (Event, error) {
	if err := s.check(); err != nil {
		return Event{}, err
	}
	var view C.gabriel_event_t
	if err := call(func() C.gabriel_status_t { return C.gabriel_subscription_poll(s.handle, &view) }); err != nil {
		return Event{}, err
	}
	return Event{
		Kind:        EventKind(view.kind),
		Lost:        uint64(view.lost),
		ID:          inputID(view.id),
		Engine:      C.GoString(view.engine),
		Flow:        C.GoString(view.flow),
		Forward:     Forward(view.forward),
		PayloadType: C.GoString(view.payload_type),
		Result:      C.GoBytes(unsafe.Pointer(view.result), C.int(view.result_len)),
		At:          Timestamp(view.at_ns),
		OfferedAt:   Timestamp(view.offered_at_ns),
		ReceivedAt:  Timestamp(view.received_at_ns),
	}, nil
}

// Next waits for the next event, until ctx is done.
func (s *Subscription) Next(ctx context.Context) (Event, error) {
	return next(ctx, s.Poll, s.Wait)
}

// Close destroys the subscription, and unlocks the goroutine from its thread.
func (s *Subscription) Close() error {
	if err := s.check(); err != nil {
		return err
	}
	err := call(func() C.gabriel_status_t { return C.gabriel_subscription_destroy(s.handle) })
	s.handle = nil
	s.thread.release()
	return err
}
