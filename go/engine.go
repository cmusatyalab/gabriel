package gabriel

// #include <stdlib.h>
// #include <gabriel/gabriel.h>
import "C"

import (
	"context"
	"errors"
	"time"
	"unsafe"
)

// cancelCheckInterval is how long [Engine.Next] and [Subscription.Next] wait at a time before
// checking whether their context is done.
const cancelCheckInterval = 100 * time.Millisecond

// Input is an input handed to an engine.
type Input struct {
	ID InputID
	// PayloadType says how to read Data; empty if the input has no convention.
	PayloadType string
	// Data is the input's bytes, in shared memory. It stays valid until the engine reports its
	// outcome; copy it to keep it longer.
	Data []byte
}

// Engine processes one input at a time and reports exactly one outcome for it: [Engine.Stop],
// [Engine.Pass] or [Engine.Emit]. A server delivers inputs to it once the server adds it
// ([Server.AddEngine]).
//
// An Engine must be used and closed in the goroutine that created it (see Threads in the package
// documentation).
type Engine struct {
	handle        *C.gabriel_engine_t
	thread        thread
	maxOutputSize int
}

// NewEngine creates the engine name. resultType names how to read its results, e.g. a protobuf
// message name; empty if it has no convention. An output and its result are together at most
// maxOutputSize bytes.
func NewEngine(name, resultType string, maxOutputSize int) (*Engine, error) {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	var cResultType *C.char
	if resultType != "" {
		cResultType = C.CString(resultType)
		defer C.free(unsafe.Pointer(cResultType))
	}
	t := lockThread()
	e := &Engine{thread: t, maxOutputSize: maxOutputSize}
	err := call(func() C.gabriel_status_t {
		return C.gabriel_engine_create(cName, cResultType, C.size_t(maxOutputSize), &e.handle)
	})
	if err != nil {
		t.release()
		return nil, err
	}
	return e, nil
}

func (e *Engine) check() error {
	if e.handle == nil {
		return failure(ErrClosed)
	}
	return e.thread.check()
}

// Wait waits until an input is ready, or timeout passes, and fails with [ErrAgain] then. The input
// itself comes from [Engine.Poll]. A wake-up without an input is possible, so poll in a loop, or use
// [Engine.Next].
func (e *Engine) Wait(timeout time.Duration) error {
	if err := e.check(); err != nil {
		return err
	}
	return call(func() C.gabriel_status_t { return C.gabriel_engine_wait(e.handle, milliseconds(timeout)) })
}

// Poll returns the next input, or fails with [ErrAgain] if none is available.
func (e *Engine) Poll() (Input, error) {
	if err := e.check(); err != nil {
		return Input{}, err
	}
	var view C.gabriel_input_t
	if err := call(func() C.gabriel_status_t { return C.gabriel_engine_poll_input(e.handle, &view) }); err != nil {
		return Input{}, err
	}
	return Input{
		ID:          inputID(view.id),
		PayloadType: C.GoString(view.payload_type),
		Data:        unsafe.Slice((*byte)(unsafe.Pointer(view.buf)), int(view.len)),
	}, nil
}

// Next waits for the next input, until ctx is done.
func (e *Engine) Next(ctx context.Context) (Input, error) {
	return next(ctx, e.Poll, e.Wait)
}

// next polls until it gets a value, waiting between polls, until ctx is done.
func next[T any](ctx context.Context, poll func() (T, error), wait func(time.Duration) error) (T, error) {
	for {
		value, err := poll()
		if !errors.Is(err, ErrAgain) {
			return value, err
		}
		if err := ctx.Err(); err != nil {
			return value, err
		}
		timeout := cancelCheckInterval
		if deadline, ok := ctx.Deadline(); ok {
			timeout = min(timeout, time.Until(deadline))
		}
		if err := wait(timeout); err != nil && !errors.Is(err, ErrAgain) {
			return value, err
		}
	}
}

// Stop reports that the engine forwards nothing. A discard is a stop with no result.
func (e *Engine) Stop(result []byte) error {
	return e.report(result, func(buf *C.uint8_t, n C.size_t) C.gabriel_status_t {
		return C.gabriel_engine_stop(e.handle, buf, n)
	})
}

// Pass reports that the engine forwards the input unchanged along every outgoing edge.
func (e *Engine) Pass(result []byte) error {
	return e.report(result, func(buf *C.uint8_t, n C.size_t) C.gabriel_status_t {
		return C.gabriel_engine_pass(e.handle, buf, n)
	})
}

func (e *Engine) report(result []byte, send func(*C.uint8_t, C.size_t) C.gabriel_status_t) error {
	if err := e.check(); err != nil {
		return err
	}
	buf, n := bytesArg(result)
	return call(func() C.gabriel_status_t { return send(buf, n) })
}

// Loan returns a buffer in shared memory for a new input, of the engine's maximum output size.
// Write the input into it, then forward it with [Engine.Emit].
func (e *Engine) Loan() ([]byte, error) {
	if err := e.check(); err != nil {
		return nil, err
	}
	var buf *C.uint8_t
	if err := call(func() C.gabriel_status_t { return C.gabriel_engine_new_output(e.handle, &buf) }); err != nil {
		return nil, err
	}
	return unsafe.Slice((*byte)(unsafe.Pointer(buf)), e.maxOutputSize), nil
}

// Emit reports that the engine forwards the first n bytes of the loaned buffer as a new input along
// every outgoing edge. outputType names how to read it; empty if it has no convention.
func (e *Engine) Emit(outputType string, n int, result []byte) error {
	if err := e.check(); err != nil {
		return err
	}
	var cOutputType *C.char
	if outputType != "" {
		cOutputType = C.CString(outputType)
		defer C.free(unsafe.Pointer(cOutputType))
	}
	buf, resultLen := bytesArg(result)
	return call(func() C.gabriel_status_t {
		return C.gabriel_engine_emit(e.handle, cOutputType, C.size_t(n), buf, resultLen)
	})
}

// Close destroys the engine, and unlocks the goroutine from its thread.
func (e *Engine) Close() error {
	if err := e.check(); err != nil {
		return err
	}
	err := call(func() C.gabriel_status_t { return C.gabriel_engine_destroy(e.handle) })
	e.handle = nil
	e.thread.release()
	return err
}
