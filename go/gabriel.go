// Package gabriel provides Go bindings for the gabriel C library.
package gabriel

/*
#cgo CFLAGS: -I${SRCDIR}/../c/include
#include <gabriel/gabriel.h>
*/
import "C"

// Version returns the gabriel library version string.
func Version() string {
	return C.GoString(C.gabriel_version())
}

// Add is a placeholder binding demonstrating the plumbing end to end.
// Replace with real API functions.
func Add(a, b int) int {
	return int(C.gabriel_add(C.int(a), C.int(b)))
}
