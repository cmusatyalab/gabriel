package gabriel

// #include <stdlib.h>
// #include <gabriel/gabriel.h>
import "C"

import (
	"sync"
	"unsafe"
)

// ClientOption configures a client that [NewClient] creates.
type ClientOption func(*clientOptions)

type clientOptions struct {
	remoteAddr       string
	certificate      []byte
	crossingCapacity *int
}

// WithRemoteServer connects the client to the remote server at addr, e.g. "192.0.2.1:4433". The
// client trusts only certificate, in DER, as [Server.Certificate] gives it.
func WithRemoteServer(addr string, certificate []byte) ClientOption {
	return func(o *clientOptions) {
		o.remoteAddr = addr
		o.certificate = certificate
	}
}

// WithCrossingCapacity sets how many inputs each crossing to the remote server may have in flight:
// on the network, or waiting at the server. It must be at least 1, and needs [WithRemoteServer].
// Without it, crossings get the default capacity.
func WithCrossingCapacity(capacity int) ClientOption {
	return func(o *clientOptions) { o.crossingCapacity = &capacity }
}

// set writes o to the C ABI's client options.
func (o *clientOptions) set(options *C.gabriel_client_options_t) error {
	if o.remoteAddr != "" {
		cAddr := C.CString(o.remoteAddr)
		defer C.free(unsafe.Pointer(cAddr))
		certificate, certificateLen := bytesArg(o.certificate)
		err := call(func() C.gabriel_status_t {
			return C.gabriel_client_options_set_remote_server(options, cAddr, certificate, certificateLen)
		})
		if err != nil {
			return err
		}
	}
	if o.crossingCapacity == nil {
		return nil
	}
	if *o.crossingCapacity < 0 {
		return &Error{kind: ErrInvalid, message: "negative crossing capacity"}
	}
	return call(func() C.gabriel_status_t {
		return C.gabriel_client_options_set_remote_crossing_capacity(options, C.size_t(*o.crossingCapacity))
	})
}

// Client bridges local producers to the servers that run a flow's engines. It may be used from any
// goroutine.
type Client struct {
	mu     sync.Mutex
	handle *C.gabriel_client_t
}

// NewClient creates the client name, of the local server localServer. Without options, it has no
// remote server.
func NewClient(name, localServer string, opts ...ClientOption) (*Client, error) {
	var o clientOptions
	for _, opt := range opts {
		opt(&o)
	}
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	cLocalServer := C.CString(localServer)
	defer C.free(unsafe.Pointer(cLocalServer))
	var options *C.gabriel_client_options_t
	if err := call(func() C.gabriel_status_t { return C.gabriel_client_options_create(&options) }); err != nil {
		return nil, err
	}
	defer C.gabriel_client_options_destroy(options)
	if err := o.set(options); err != nil {
		return nil, err
	}
	c := &Client{}
	err := call(func() C.gabriel_status_t {
		return C.gabriel_client_create(cName, cLocalServer, options, &c.handle)
	})
	if err != nil {
		return nil, err
	}
	return c, nil
}

// withName calls f on the client with name as a C string.
func (c *Client) withName(name string, f func(*C.char) C.gabriel_status_t) error {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.handle == nil {
		return failure(ErrClosed)
	}
	return call(func() C.gabriel_status_t { return f(cName) })
}

// AddProducer takes the inputs of the producer name.
func (c *Client) AddProducer(name string) error {
	return c.withName(name, func(n *C.char) C.gabriel_status_t {
		return C.gabriel_client_add_producer(c.handle, n)
	})
}

// RemoveProducer stops taking the inputs of the producer name. It fails while a flow uses it.
func (c *Client) RemoveProducer(name string) error {
	return c.withName(name, func(n *C.char) C.gabriel_status_t {
		return C.gabriel_client_remove_producer(c.handle, n)
	})
}

// AddFlow adds the flow name. spec describes it in TOML: its producers, its engines and where each
// runs, and the edges between them, e.g.
//
//	[[producers]]
//	name = "camera"
//	tokens = 2
//
//	[[engines]]
//	name = "detector"
//	location = "local"
//
//	[[edges]]
//	from = "camera"
//	to = "detector"
//	timeout = "100ms"
//
// The client must take the inputs of every producer ([Client.AddProducer]), and a server must serve
// every engine ([Server.AddEngine]).
func (c *Client) AddFlow(name, spec string) error {
	cSpec := C.CString(spec)
	defer C.free(unsafe.Pointer(cSpec))
	return c.withName(name, func(n *C.char) C.gabriel_status_t {
		return C.gabriel_client_add_flow(c.handle, n, cSpec)
	})
}

// RemoveFlow removes the flow name.
func (c *Client) RemoveFlow(name string) error {
	return c.withName(name, func(n *C.char) C.gabriel_status_t {
		return C.gabriel_client_remove_flow(c.handle, n)
	})
}

// Close destroys the client, which removes its flows.
func (c *Client) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.handle == nil {
		return failure(ErrClosed)
	}
	err := call(func() C.gabriel_status_t { return C.gabriel_client_destroy(c.handle) })
	c.handle = nil
	return err
}
