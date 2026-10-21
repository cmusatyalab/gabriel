"""Python bindings for Gabriel, through its C ABI (rust/include/gabriel/gabriel.h).

A producer publishes inputs on a flow. A client takes the producer's inputs and offers them to the
flow's engines, which a server serves. Each engine processes one input at a time and reports one
outcome for it: `Engine.drop`, `Engine.forward` or `Engine.replace`. The app reads what happened to
its producer's inputs from a `Subscription`, and anyone can read a producer's inputs themselves with
a `Reader`.

A `Producer`, an `Engine`, a `Subscription` and a `Reader` must stay on the thread that created
them; using one from another thread raises `WrongThreadError`. A `Client` and a `Server` may be
used from any thread. Close every handle when done with it, or use it in a `with` block.
"""

from gabriel._ffi import check as _check
from gabriel._ffi import cstr as _cstr
from gabriel._ffi import lib as _lib
from gabriel.client import Client, RemoteServer
from gabriel.engine import Engine
from gabriel.errors import (
    AgainError,
    ClosedError,
    GabrielError,
    InternalError,
    InvalidArgumentError,
    SizeExceededError,
    WrongThreadError,
)
from gabriel.producer import Producer
from gabriel.reader import Reader
from gabriel.server import Server
from gabriel.subscription import Subscription
from gabriel.types import Event, EventKind, Input, InputId, Outcome, ReadInput


def init_log(filter: str | None = None) -> None:
    """Prints Gabriel's log to standard error. `filter` picks what is printed, in the syntax of
    RUST_LOG, e.g. "info" or "gabriel=debug". If it is None, RUST_LOG picks, or warnings and errors
    are printed if that is unset. Call it once, before anything else."""
    _check(_lib.gabriel_log_init(_cstr(filter)))


def serve_metrics(addr: str) -> None:
    """Serves the metrics of Gabriel's clients and servers in this process to Prometheus, at
    http://<addr>/metrics, e.g. "0.0.0.0:9464". Call it once, before creating clients and
    servers."""
    _check(_lib.gabriel_metrics_serve(_cstr(addr)))


__all__ = [
    "AgainError",
    "Client",
    "ClosedError",
    "Engine",
    "Event",
    "EventKind",
    "GabrielError",
    "Input",
    "InputId",
    "InternalError",
    "InvalidArgumentError",
    "Outcome",
    "Producer",
    "ReadInput",
    "Reader",
    "RemoteServer",
    "Server",
    "SizeExceededError",
    "Subscription",
    "WrongThreadError",
    "init_log",
    "serve_metrics",
]
