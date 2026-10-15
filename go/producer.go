package gabriel

// #include <stdlib.h>
// #include <gabriel/gabriel.h>
import "C"

import (
	"fmt"
	"unsafe"
)

// Producer publishes inputs. An input is written in place into a loaned shared memory buffer
// ([Producer.Loan]) and then published ([Producer.Publish]); [Producer.Send] does both from a slice.
//
// A Producer must be used and closed in the goroutine that created it (see Threads in the package
// documentation).
type Producer struct {
	handle       *C.gabriel_producer_t
	thread       thread
	maxInputSize int
	session      uint64
}

// NewProducer creates the producer name, whose inputs are at most maxInputSize bytes. inputType
// names how to read its inputs, e.g. a protobuf message name; "" if they have no convention.
// Engines see it as each input's payload type. A client takes its inputs once it adds the
// producer ([Client.AddProducer]).
func NewProducer(name, inputType string, maxInputSize int) (*Producer, error) {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	var cInputType *C.char
	if inputType != "" {
		cInputType = C.CString(inputType)
		defer C.free(unsafe.Pointer(cInputType))
	}
	t := lockThread()
	p := &Producer{thread: t, maxInputSize: maxInputSize}
	err := call(func() C.gabriel_status_t {
		return C.gabriel_producer_create(cName, cInputType, C.size_t(maxInputSize), &p.handle)
	})
	if err != nil {
		t.release()
		return nil, err
	}
	var session C.uint64_t
	if err := call(func() C.gabriel_status_t { return C.gabriel_producer_session(p.handle, &session) }); err != nil {
		p.Close()
		return nil, err
	}
	p.session = uint64(session)
	return p, nil
}

// Session is chosen randomly when the producer starts; the ids of its inputs carry it ([InputID]).
func (p *Producer) Session() uint64 {
	return p.session
}

func (p *Producer) check() error {
	if p.handle == nil {
		return failure(ErrClosed)
	}
	return p.thread.check()
}

// Loan returns a buffer in shared memory for the next input, of the producer's maximum input size.
// Write the input into it, then publish it with [Producer.Publish]. The buffer is valid until then.
func (p *Producer) Loan() ([]byte, error) {
	if err := p.check(); err != nil {
		return nil, err
	}
	var buf *C.uint8_t
	err := call(func() C.gabriel_status_t { return C.gabriel_producer_new_input(p.handle, &buf) })
	if err != nil {
		return nil, err
	}
	return unsafe.Slice((*byte)(unsafe.Pointer(buf)), p.maxInputSize), nil
}

// Publish publishes the first n bytes of the loaned buffer on flow, and returns the input's sequence
// number, which the events about the input carry ([InputID]).
func (p *Producer) Publish(flow string, n int) (uint64, error) {
	if err := p.check(); err != nil {
		return 0, err
	}
	cFlow := C.CString(flow)
	defer C.free(unsafe.Pointer(cFlow))
	var seq C.uint64_t
	err := call(func() C.gabriel_status_t {
		return C.gabriel_producer_publish_input(p.handle, C.size_t(n), cFlow, &seq)
	})
	return uint64(seq), err
}

// Send copies data into a loaned buffer and publishes it on flow. It fails with [ErrSizeExceeded]
// if data is larger than the producer's maximum input size.
func (p *Producer) Send(flow string, data []byte) (uint64, error) {
	if len(data) > p.maxInputSize {
		return 0, &Error{
			kind:    ErrSizeExceeded,
			message: fmt.Sprintf("input of %d bytes exceeds maximum of %d", len(data), p.maxInputSize),
		}
	}
	buf, err := p.Loan()
	if err != nil {
		return 0, err
	}
	return p.Publish(flow, copy(buf, data))
}

// Close destroys the producer, and unlocks the goroutine from its thread.
func (p *Producer) Close() error {
	if err := p.check(); err != nil {
		return err
	}
	err := call(func() C.gabriel_status_t { return C.gabriel_producer_destroy(p.handle) })
	p.handle = nil
	p.thread.release()
	return err
}
