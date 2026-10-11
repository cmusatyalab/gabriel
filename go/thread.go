package gabriel

import (
	"runtime"
	"syscall"
)

// thread pins a handle to the OS thread that created it. Creating one locks the calling goroutine
// to its thread until release, so no other goroutine runs on that thread meanwhile: a call made on
// that thread comes from the goroutine that created the handle.
type thread struct {
	id int
}

func lockThread() thread {
	runtime.LockOSThread()
	return thread{id: syscall.Gettid()}
}

// check fails unless the calling goroutine is the one that created the handle.
func (t thread) check() error {
	if syscall.Gettid() != t.id {
		return failure(ErrWrongThread)
	}
	return nil
}

func (t thread) release() {
	runtime.UnlockOSThread()
}
