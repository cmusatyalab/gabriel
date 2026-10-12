"""Servers: deliver a flow's inputs to its engines."""

import os

from gabriel._ffi import LockedHandle, check, create, cstr, ffi, lib


class Server(LockedHandle):
    """Delivers the inputs of the flows installed on it to a dynamic set of engines. It may be used
    from any thread."""

    def __init__(
        self,
        name: str,
        bind_addr: str | None = None,
        identity_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        """Creates the server `name`. `bind_addr` is the network address to accept remote clients
        on, e.g. "0.0.0.0:4433", or None for a server with local clients only.

        `identity_dir` is an existing directory where a remote server keeps its certificate and key, so that they stay
        the same across restarts and its clients can reconnect. The server makes them there the
        first time. With None, it makes a new certificate each time it starts."""
        handle = create(
            lib.gabriel_server_create,
            "gabriel_server_t **",
            cstr(name),
            cstr(bind_addr),
            cstr(None if identity_dir is None else os.fspath(identity_dir)),
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
