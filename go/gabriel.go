// Package gabriel provides Go bindings for Gabriel, through its C ABI (rust/include/gabriel/gabriel.h).
//
// A producer publishes inputs on a flow. A client takes the producer's inputs and offers them to the
// flow's engines, which a server serves. Each engine processes one input at a time and reports one
// outcome for it: [Engine.Stop], [Engine.Pass] or [Engine.Emit]. The app reads what happened to its
// producer's inputs from a [Subscription].
//
// # Threads
//
// A [Producer], an [Engine] and a [Subscription] must stay on the OS thread that created them. Their
// constructors lock the calling goroutine to its thread (runtime.LockOSThread), and Close unlocks
// it, so use and close each one in the goroutine that created it. A call from another goroutine
// fails with [ErrWrongThread]. A [Client] and a [Server] may be used from any goroutine.
//
// Close every handle when done with it: none is closed by the garbage collector.
//
// # Building
//
// The package links the Rust library statically. Build it before using the package:
//
//	go generate github.com/cmusatyalab/gabriel/go
//
// which runs cargo build --release -p gabriel.
package gabriel

//go:generate cargo build --release -p gabriel

/*
#cgo CFLAGS: -I${SRCDIR}/../rust/include
#cgo LDFLAGS: ${SRCDIR}/../target/release/libgabriel.a -lgcc_s -lutil -lrt -lpthread -lm -ldl
#include <stdlib.h>
#include <time.h>
#include <gabriel/gabriel.h>

static uint64_t gabriel_go_now_ns(void) {
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec;
}
*/
import "C"

import (
	"errors"
	"fmt"
	"runtime"
	"time"
	"unsafe"
)

// Errors that a call can fail with. Test for them with [errors.Is]; the error itself also says what
// went wrong.
var (
	// ErrInvalid means an argument was invalid, e.g. an unknown name or a malformed flow.
	ErrInvalid = errors.New("invalid argument")
	// ErrSizeExceeded means a payload is larger than its maximum size.
	ErrSizeExceeded = errors.New("size exceeded")
	// ErrAgain means nothing is available yet: no input or event, or a wait timed out.
	ErrAgain = errors.New("nothing available, try again")
	// ErrInternal means Gabriel failed internally, e.g. in its shared memory transport.
	ErrInternal = errors.New("internal error")
	// ErrWrongThread means a producer, engine or subscription was used from a goroutine other than
	// the one that created it.
	ErrWrongThread = errors.New("used from a goroutine other than the one that created it")
	// ErrClosed means the handle was already closed.
	ErrClosed = errors.New("already closed")
)

// The library is linked statically, but a stale build of it can still be linked against a newer
// header. Its ABI version says so before any call goes wrong.
func init() {
	if version := C.gabriel_abi_version(); version != C.GABRIEL_ABI_VERSION {
		panic(fmt.Sprintf("gabriel: the library implements ABI version %d, but the header is version %d; "+
			"run go generate github.com/cmusatyalab/gabriel/go", version, C.GABRIEL_ABI_VERSION))
	}
}

// Error is a failed call into Gabriel. It wraps one of the errors above.
type Error struct {
	kind    error
	message string
}

func (e *Error) Error() string { return "gabriel: " + e.message }

func (e *Error) Unwrap() error { return e.kind }

// call runs f, which calls into the C ABI, and returns its failure as an error. The calling
// goroutine stays on its thread until the message of the failure is read, since the message is
// kept per thread.
func call(f func() C.gabriel_status_t) error {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	kind := statusError(f())
	if kind == nil {
		return nil
	}
	return &Error{kind: kind, message: C.GoString(C.gabriel_last_error())}
}

func statusError(status C.gabriel_status_t) error {
	switch status {
	case C.GABRIEL_STATUS_T_OK:
		return nil
	case C.GABRIEL_STATUS_T_ERR_INVALID:
		return ErrInvalid
	case C.GABRIEL_STATUS_T_ERR_SIZE_EXCEEDED:
		return ErrSizeExceeded
	case C.GABRIEL_STATUS_T_ERR_AGAIN:
		return ErrAgain
	default:
		return ErrInternal
	}
}

// failure is an error raised by these bindings rather than by the C ABI.
func failure(kind error) error {
	return &Error{kind: kind, message: kind.Error()}
}

// InitLog prints Gabriel's log to standard error. filter picks what is printed, in the syntax of
// RUST_LOG, e.g. "info" or "gabriel=debug". If it is empty, RUST_LOG picks, or warnings and errors
// are printed if that is unset. Call it once, before anything else; without it, nothing is logged.
func InitLog(filter string) error {
	var cFilter *C.char
	if filter != "" {
		cFilter = C.CString(filter)
		defer C.free(unsafe.Pointer(cFilter))
	}
	return call(func() C.gabriel_status_t { return C.gabriel_log_init(cFilter) })
}

// Timestamp is a reading of the machine's monotonic clock (CLOCK_MONOTONIC), the clock every event
// is stamped with. Timestamps from processes on one machine compare directly.
type Timestamp time.Duration

// Now reads the monotonic clock.
func Now() Timestamp {
	return Timestamp(C.gabriel_go_now_ns())
}

// Sub returns the time from u to t.
func (t Timestamp) Sub(u Timestamp) time.Duration {
	return time.Duration(t - u)
}

// IsZero reports whether t is unset.
func (t Timestamp) IsZero() bool {
	return t == 0
}

// InputID identifies one input of a producer.
type InputID struct {
	// Producer is the producer's name.
	Producer string
	// Session is chosen randomly when the producer starts, so ids never repeat across restarts.
	Session uint64
	// Seq is the input's sequence number, as [Producer.Publish] returns it.
	Seq uint64
}

func inputID(id C.gabriel_input_id_t) InputID {
	return InputID{Producer: C.GoString(id.producer), Session: uint64(id.session), Seq: uint64(id.seq)}
}

// milliseconds converts timeout for the C ABI, rounding up so a short wait is not a poll, and
// capping it at the largest the ABI takes.
func milliseconds(timeout time.Duration) C.uint32_t {
	ms := (timeout + time.Millisecond - 1) / time.Millisecond
	return C.uint32_t(min(max(ms, 0), time.Duration(^uint32(0))))
}

// bytesArg passes b to the C ABI without copying it. C must not keep it past the call.
func bytesArg(b []byte) (*C.uint8_t, C.size_t) {
	if len(b) == 0 {
		return nil, 0
	}
	return (*C.uint8_t)(unsafe.Pointer(unsafe.SliceData(b))), C.size_t(len(b))
}
