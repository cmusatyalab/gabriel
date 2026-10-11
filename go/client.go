package gabriel

// #include <stdlib.h>
// #include <gabriel/gabriel.h>
import "C"

import (
	"sync"
	"unsafe"
)

// RemoteServer is how a client reaches a remote server.
type RemoteServer struct {
	// Addr is the server's address, e.g. "192.0.2.1:4433".
	Addr string
	// Certificate is the server's certificate, in DER, as [Server.Certificate] gives it. The client
	// trusts only this certificate.
	Certificate []byte
}

// Client bridges local producers to the servers that run a flow's engines. It may be used from any
// goroutine.
type Client struct {
	mu     sync.Mutex
	handle *C.gabriel_client_t
}

// NewClient creates the client name, of the local server localServer. remote is the remote server,
// or nil for none.
func NewClient(name, localServer string, remote *RemoteServer) (*Client, error) {
	cName := C.CString(name)
	defer C.free(unsafe.Pointer(cName))
	cLocalServer := C.CString(localServer)
	defer C.free(unsafe.Pointer(cLocalServer))
	var cAddr *C.char
	var certificate []byte
	if remote != nil {
		cAddr = C.CString(remote.Addr)
		defer C.free(unsafe.Pointer(cAddr))
		certificate = remote.Certificate
	}
	certificateBuf, certificateLen := bytesArg(certificate)
	c := &Client{}
	err := call(func() C.gabriel_status_t {
		return C.gabriel_client_create(cName, cLocalServer, cAddr, certificateBuf, certificateLen, &c.handle)
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
		return C.gabriel_client_add_producer(n, c.handle)
	})
}

// RemoveProducer stops taking the inputs of the producer name. It fails while a flow uses it.
func (c *Client) RemoveProducer(name string) error {
	return c.withName(name, func(n *C.char) C.gabriel_status_t {
		return C.gabriel_client_remove_producer(n, c.handle)
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
		return C.gabriel_client_add_flow(n, cSpec, c.handle)
	})
}

// RemoveFlow removes the flow name.
func (c *Client) RemoveFlow(name string) error {
	return c.withName(name, func(n *C.char) C.gabriel_status_t {
		return C.gabriel_client_remove_flow(n, c.handle)
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
