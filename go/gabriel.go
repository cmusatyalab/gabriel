// Package gabriel provides Go bindings for Gabriel, built on the
// Lightning C transport library.
package gabriel

/*
#cgo CFLAGS: -D_GNU_SOURCE -I${SRCDIR}/../c/include
#cgo LDFLAGS: -lpthread
#include <lightning/lightning.h>
*/
import "C"

// LightningVersion returns the version string of the underlying
// Lightning C library.
func LightningVersion() string {
	return C.GoString(C.lightning_version())
}
