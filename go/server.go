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

// ServerOption configures a server that [NewServer] creates.
type ServerOption func(*serverOptions)

type serverOptions struct {
	bindAddr    string
	identityDir string
}

// WithBindAddr makes the server accept remote clients on the network address addr, e.g.
// "0.0.0.0:4433". Without it, the server has local clients only.
func WithBindAddr(addr string) ServerOption {
	return func(o *serverOptions) { o.bindAddr = addr }
}

// WithIdentityDir makes a server that accepts remote clients keep its certificate and key in dir,
// an existing directory, so that they stay the same across restarts and its clients can reconnect.
// The server makes them there the first time. Without it, the server makes a new certificate each
// time it starts. It needs [WithBindAddr].
func WithIdentityDir(dir string) ServerOption {
	return func(o *serverOptions) { o.identityDir = dir }
}

// set writes o to the C ABI's server options.
func (o *serverOptions) set(options *C.gabriel_server_options_t) error {
	if o.bindAddr != "" {
		cBindAddr := C.CString(o.bindAddr)
		defer C.free(unsafe.Pointer(cBindAddr))
		err := call(func() C.gabriel_status_t { return C.gabriel_server_options_set_bind_addr(options, cBindAddr) })
		if err != nil {
			return err
		}
	}
	if o.identityDir == "" {
		return nil
	}
	cIdentityDir := C.CString(o.identityDir)
	defer C.free(unsafe.Pointer(cIdentityDir))
	return call(func() C.gabriel_status_t {
		return C.gabriel_server_options_set_identity_dir(options, cIdentityDir)
	})
}

// NewServer creates the server name. Without options, it has local clients only.
func NewServer(name string, opts ...ServerOption) (*Server, error) {
	var o serverOptions
	for _, opt := range opts {
		opt(&o)
	}
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	var options *C.gabriel_server_options_t
	if err := call(func() C.gabriel_status_t { return C.gabriel_server_options_create(&options) }); err != nil {
		return nil, err
	}
	defer C.gabriel_server_options_destroy(options)
	if err := o.set(options); err != nil {
		return nil, err
	}
	s := &Server{}
	err := call(func() C.gabriel_status_t {
		return C.gabriel_server_create(cName, options, &s.handle)
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
	return s.locked(func() C.gabriel_status_t { return C.gabriel_server_add_engine(s.handle, cName) })
}

// RemoveEngine stops serving the engine name.
func (s *Server) RemoveEngine(name string) error {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	return s.locked(func() C.gabriel_status_t { return C.gabriel_server_remove_engine(s.handle, cName) })
}

// RemotePort returns the port remote clients reach the server on. It fails for a server created
// without a bind address.
func (s *Server) RemotePort() (uint16, error) {
	var port C.uint16_t
	err := s.locked(func() C.gabriel_status_t { return C.gabriel_server_remote_port(s.handle, &port) })
	return uint16(port), err
}

// Certificate returns the certificate remote clients must trust, in DER ([WithRemoteServer]). It fails
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
