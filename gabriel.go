// Package gabriel is the public API for the Gabriel core skeleton. It
// wraps the cgo/C++ implementation in internal/ccore; no cgo type is
// ever exposed here.
package gabriel

import (
	"fmt"

	"github.com/cmusatyalab/gabriel/api/go/fbs"
	"github.com/cmusatyalab/gabriel/internal/ccore"
)

// Greeting is a decoded copy of the FlatBuffer built by the C++ core.
type Greeting struct {
	ID      int32
	Message string
}

// NewGreeting builds a Greeting FlatBuffer in C++ (via internal/ccore)
// and decodes it back with the Go FlatBuffers runtime, demonstrating the
// full C++-to-Go roundtrip.
func NewGreeting(id int32, message string) (*Greeting, error) {
	buf, err := ccore.BuildGreeting(id, message)
	if err != nil {
		return nil, fmt.Errorf("gabriel: %w", err)
	}

	g := fbs.GetRootAsGreeting(buf, 0)
	return &Greeting{
		ID:      g.Id(),
		Message: string(g.Message()),
	}, nil
}
