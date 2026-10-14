"""Clients: bridge local producers to the servers that run a flow's engines."""

from dataclasses import dataclass

from gabriel._ffi import LockedHandle, bytes_arg, check, create, cstr, lib, options
from gabriel.errors import InvalidArgumentError


@dataclass(frozen=True, slots=True)
class RemoteServer:
    """How a client reaches a remote server."""

    addr: str
    """The server's address, e.g. "192.0.2.1:4433"."""
    certificate: bytes
    """The server's certificate, in DER, as `Server.certificate` gives it. The client trusts only
    this certificate."""
    crossing_capacity: int | None = None
    """How many inputs each crossing to the server may have in flight: on the network, or waiting
    at the server. At least 1; None for the default."""


class Client(LockedHandle):
    """Bridges local producers to the servers that run a flow's engines. It may be used from any
    thread."""

    def __init__(
        self, name: str, local_server: str, *, remote: RemoteServer | None = None
    ) -> None:
        """Creates the client `name`, of the local server `local_server`, and of `remote`, if
        given."""
        with options("client") as opts:
            if remote is not None:
                check(
                    lib.gabriel_client_options_set_remote_server(
                        opts, cstr(remote.addr), *bytes_arg(remote.certificate)
                    )
                )
                if remote.crossing_capacity is not None:
                    if remote.crossing_capacity < 0:
                        raise InvalidArgumentError("negative crossing capacity")
                    check(
                        lib.gabriel_client_options_set_remote_crossing_capacity(
                            opts, remote.crossing_capacity
                        )
                    )
            handle = create(
                lib.gabriel_client_create,
                "gabriel_client_t **",
                cstr(name),
                cstr(local_server),
                opts,
            )
        super().__init__(handle, lib.gabriel_client_destroy)

    def add_producer(self, name: str) -> None:
        """Takes the inputs of the producer `name`."""
        self._call(lib.gabriel_client_add_producer, cstr(name))

    def remove_producer(self, name: str) -> None:
        """Stops taking the inputs of the producer `name`. It fails while a flow uses it."""
        self._call(lib.gabriel_client_remove_producer, cstr(name))

    def add_flow(self, name: str, spec: str) -> None:
        """Adds the flow `name`. `spec` describes it in TOML: its producers, its engines and where
        each runs, and the edges between them::

            [[producers]]
            name = "camera"
            tokens = 2

            [[engines]]
            name = "detector"
            location = "local"

            [[edges]]
            from = "camera"
            to = "detector"
            timeout = "100ms"

        The client must take the inputs of every producer (`add_producer`), and a server must serve
        every engine (`Server.add_engine`).
        """
        self._call(lib.gabriel_client_add_flow, cstr(name), cstr(spec))

    def remove_flow(self, name: str) -> None:
        """Removes the flow `name`."""
        self._call(lib.gabriel_client_remove_flow, cstr(name))
