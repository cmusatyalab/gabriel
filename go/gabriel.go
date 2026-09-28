// Package gabriel provides Go bindings for Gabriel, built on the
// Gabriel C library.
package gabriel

/*
#cgo CFLAGS: -D_GNU_SOURCE -I${SRCDIR}/../c/include
#cgo LDFLAGS: -lpthread
#include <gabriel/gabriel.h>
*/
import "C"

// Version returns the version string of the underlying Gabriel C
// library.
func Version() string {
	return C.GoString(C.gabriel_version())
}
