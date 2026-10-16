package gabriel

// #include <stdlib.h>
// #include <gabriel/gabriel.h>
import "C"

import (
	"context"
	"time"
	"unsafe"
)

// ReadInput is an input a [Reader] read, as its producer published it.
type ReadInput struct {
	ID InputID
	// Flow is the flow the producer published the input on.
	Flow string
	// PayloadType says how to read Data; empty if the input has no convention.
	PayloadType string
	// Data is the input's bytes, in shared memory. It stays valid until the next poll; copy it to
	// keep it longer.
	Data []byte
	// Lost is how many inputs of the producer's session were lost right before this one, because
	// the reader fell behind.
	Lost uint64
}

// Reader reads the inputs that one producer publishes, in order, alongside the client that takes
// them. Unlike a [Subscription] to the producer, which reads what happened to its inputs, it reads
// the inputs themselves. A reader that falls behind loses the oldest, and learns how many from
// [ReadInput.Lost].
//
// A Reader must be used and closed in the goroutine that created it (see Threads in the package
// documentation).
type Reader struct {
	handle *C.gabriel_reader_t
	thread thread
}

// NewReader reads the inputs of the producer name. The producer need not exist yet. Only inputs
// published from now on are read.
func NewReader(name string) (*Reader, error) {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	t := lockThread()
	r := &Reader{thread: t}
	err := call(func() C.gabriel_status_t { return C.gabriel_reader_create(cName, &r.handle) })
	if err != nil {
		t.release()
		return nil, err
	}
	return r, nil
}

func (r *Reader) check() error {
	if r.handle == nil {
		return failure(ErrClosed)
	}
	return r.thread.check()
}

// Wait waits until an input is ready, or timeout passes, and fails with [ErrAgain] then. The input
// itself comes from [Reader.Poll].
func (r *Reader) Wait(timeout time.Duration) error {
	if err := r.check(); err != nil {
		return err
	}
	return call(func() C.gabriel_status_t { return C.gabriel_reader_wait(r.handle, milliseconds(timeout)) })
}

// Poll returns the next input, or fails with [ErrAgain] if none is available. Its Data stays valid
// until the next poll.
func (r *Reader) Poll() (ReadInput, error) {
	if err := r.check(); err != nil {
		return ReadInput{}, err
	}
	var view C.gabriel_read_input_t
	if err := call(func() C.gabriel_status_t { return C.gabriel_reader_poll(r.handle, &view) }); err != nil {
		return ReadInput{}, err
	}
	return ReadInput{
		ID:          inputID(view.id),
		Flow:        C.GoString(view.flow),
		PayloadType: C.GoString(view.payload_type),
		Data:        unsafe.Slice((*byte)(unsafe.Pointer(view.buf)), int(view.len)),
		Lost:        uint64(view.lost),
	}, nil
}

// Next waits for the next input, until ctx is done.
func (r *Reader) Next(ctx context.Context) (ReadInput, error) {
	return next(ctx, r.Poll, r.Wait)
}

// Close destroys the reader, and unlocks the goroutine from its thread.
func (r *Reader) Close() error {
	if err := r.check(); err != nil {
		return err
	}
	err := call(func() C.gabriel_status_t { return C.gabriel_reader_destroy(r.handle) })
	r.handle = nil
	r.thread.release()
	return err
}
