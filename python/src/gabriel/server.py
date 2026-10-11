"""Servers: deliver a flow's inputs to its engines."""

from gabriel._ffi import LockedHandle, check, create, cstr, ffi, lib


class Server(LockedHandle):
    """Delivers the inputs of the flows installed on it to a dynamic set of engines. It may be used
    from any thread."""

    def __init__(self, name: str, bind_addr: str | None = None) -> None:
        """Creates the server `name`. `bind_addr` is the network address to accept remote clients
        on, e.g. "0.0.0.0:4433", or None for a server with local clients only."""
        handle = create(
            lib.gabriel_server_create, "gabriel_server_t **", cstr(name), cstr(bind_addr)
        )
        super().__init__(handle, lib.gabriel_server_destroy)

    def add_engine(self, name: str) -> None:
        """Serves the engine `name`, which must exist (`Engine`)."""
        self._call(lib.gabriel_server_add_engine, cstr(name))

    def remove_engine(self, name: str) -> None:
        """Stops serving the engine `name`."""
        self._call(lib.gabriel_server_remove_engine, cstr(name))

    @property
    def remote_port(self) -> int:
        """The port remote clients reach the server on. Raises for a server without a bind
        address."""
        port = ffi.new("uint16_t *")
        with self._lock:
            check(lib.gabriel_server_remote_port(self._live(), port))
        return port[0]

    @property
    def certificate(self) -> bytes:
        """The certificate remote clients must trust, in DER (`RemoteServer`). Raises for a server
        without a bind address."""
        certificate, length = ffi.new("const uint8_t **"), ffi.new("size_t *")
        with self._lock:
            check(lib.gabriel_server_certificate(self._live(), certificate, length))
            return bytes(ffi.buffer(certificate[0], length[0]))
