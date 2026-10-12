package gabriel

// #include <stdlib.h>
// #include <gabriel/gabriel.h>
import "C"

import (
	"sync"
	"unsafe"
)

// Server delivers the inputs of the flows installed on it to a dynamic set of engines. It may be
// used from any goroutine.
type Server struct {
	mu     sync.Mutex
	handle *C.gabriel_server_t
}

// NewServer creates the server name. bindAddr is the network address to accept remote clients on,
// e.g. "0.0.0.0:4433", or empty for a server with local clients only.
//
// identityDir is an existing directory where a remote server keeps its certificate and key, so that they stay the same
// across restarts and its clients can reconnect. The server makes them there the first time. If it
// is empty, the server makes a new certificate each time it starts.
func NewServer(name, bindAddr, identityDir string) (*Server, error) {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	var cBindAddr, cIdentityDir *C.char
	if bindAddr != "" {
		cBindAddr = C.CString(bindAddr)
		defer C.free(unsafe.Pointer(cBindAddr))
	}
	if identityDir != "" {
		cIdentityDir = C.CString(identityDir)
		defer C.free(unsafe.Pointer(cIdentityDir))
	}
	s := &Server{}
	err := call(func() C.gabriel_status_t {
		return C.gabriel_server_create(cName, cBindAddr, cIdentityDir, &s.handle)
	})
	if err != nil {
		return nil, err
	}
	return s, nil
}

// locked calls f with the server's lock held.
func (s *Server) locked(f func() C.gabriel_status_t) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.handle == nil {
		return failure(ErrClosed)
	}
	return call(f)
}

// AddEngine serves the engine name, which must exist ([NewEngine]).
func (s *Server) AddEngine(name string) error {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	return s.locked(func() C.gabriel_status_t { return C.gabriel_server_add_engine(cName, s.handle) })
}

// RemoveEngine stops serving the engine name.
func (s *Server) RemoveEngine(name string) error {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	return s.locked(func() C.gabriel_status_t { return C.gabriel_server_remove_engine(cName, s.handle) })
}

// RemotePort returns the port remote clients reach the server on. It fails for a server created
// without a bind address.
func (s *Server) RemotePort() (uint16, error) {
	var port C.uint16_t
	err := s.locked(func() C.gabriel_status_t { return C.gabriel_server_remote_port(s.handle, &port) })
	return uint16(port), err
}

// Certificate returns the certificate remote clients must trust, in DER ([RemoteServer]). It fails
// for a server created without a bind address.
func (s *Server) Certificate() ([]byte, error) {
	var certificate *C.uint8_t
	var n C.size_t
	err := s.locked(func() C.gabriel_status_t {
		return C.gabriel_server_certificate(s.handle, &certificate, &n)
	})
	if err != nil {
		return nil, err
	}
	return C.GoBytes(unsafe.Pointer(certificate), C.int(n)), nil
}

// Close stops the server.
func (s *Server) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.handle == nil {
		return failure(ErrClosed)
	}
	err := call(func() C.gabriel_status_t { return C.gabriel_server_destroy(s.handle) })
	s.handle = nil
	return err
}
