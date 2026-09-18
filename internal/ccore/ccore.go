// Package ccore is the only place in this module that compiles C++.
// It wraps the FlatBuffers/GStreamer core and exposes a pure-Go API —
// no cgo pointer is ever returned from an exported function.
package ccore

/*
#cgo CXXFLAGS: -std=c++17 -I${SRCDIR}/../../api/cpp
#cgo pkg-config: gstreamer-1.0
#include <stdlib.h>
#include "gabriel_core.h"
*/
import "C"

import (
	"errors"
	"unsafe"
)

func init() {
	C.gabriel_core_init()
}

// BuildGreeting builds a FlatBuffer-encoded Greeting{id, message} in C++
// and returns a copy of the encoded bytes.
func BuildGreeting(id int32, message string) ([]byte, error) {
	cMessage := C.CString(message)
	defer C.free(unsafe.Pointer(cMessage))

	var outBuf *C.uint8_t
	var outLen C.size_t

	ok := C.gabriel_build_greeting(C.int32_t(id), cMessage, &outBuf, &outLen)
	if ok == 0 {
		return nil, errors.New("ccore: gabriel_build_greeting failed")
	}
	defer C.gabriel_free_buffer(outBuf)

	return C.GoBytes(unsafe.Pointer(outBuf), C.int(outLen)), nil
}
