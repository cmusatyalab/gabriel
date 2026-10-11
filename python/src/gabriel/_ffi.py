"""Loads Gabriel's C library, and turns its status codes into exceptions.

The library is ``libgabriel.so``, which ``cargo build --release -p gabriel`` builds, and its
declarations come from ``gabriel_cffi.h``, which the same build writes. Both are found in the
repository this package lives in, unless ``GABRIEL_LIBRARY`` names the library.
"""

import math
import os
import threading
import time
from pathlib import Path

from cffi import FFI

from gabriel.errors import (
    AgainError,
    ClosedError,
    GabrielError,
    InternalError,
    InvalidArgumentError,
    SizeExceededError,
    WrongThreadError,
)

LIBRARY_ENV = "GABRIEL_LIBRARY"
_REPOSITORY = Path(__file__).resolve().parents[3]
_LIBRARY = _REPOSITORY / "target" / "release" / "libgabriel.so"
_HEADER = _REPOSITORY / "rust" / "include" / "gabriel" / "gabriel_cffi.h"

ffi = FFI()
ffi.cdef(_HEADER.read_text())
lib = ffi.dlopen(os.environ.get(LIBRARY_ENV, str(_LIBRARY)))

_ERRORS: dict[int, type[GabrielError]] = {
    lib.GABRIEL_STATUS_T_ERR_INVALID: InvalidArgumentError,
    lib.GABRIEL_STATUS_T_ERR_SIZE_EXCEEDED: SizeExceededError,
    lib.GABRIEL_STATUS_T_ERR_AGAIN: AgainError,
    lib.GABRIEL_STATUS_T_ERR_INTERNAL: InternalError,
}

# The longest wait the C ABI takes, in milliseconds.
_MAX_TIMEOUT_MS = 2**32 - 1
_MS_PER_SECOND = 1000


def check(status: int) -> None:
    """Raises the error for `status`, with the message of the failure, unless it is OK.

    A Python thread stays on one OS thread, so the message read here is the one of the call that
    just failed on this thread.
    """
    if status == lib.GABRIEL_STATUS_T_OK:
        return
    message = ffi.string(lib.gabriel_last_error()).decode()
    raise _ERRORS.get(status, InternalError)(message)


def milliseconds(timeout: float) -> int:
    """Converts `timeout`, in seconds, for the C ABI: rounded up, so a short wait is not a poll."""
    return min(max(math.ceil(timeout * _MS_PER_SECOND), 0), _MAX_TIMEOUT_MS)


def cstr(text: str | None):
    """Encodes `text` as a C string, or NULL for None."""
    return ffi.NULL if text is None else text.encode()


def bytes_arg(data) -> tuple:
    """Passes any bytes-like object to the C ABI without copying it."""
    if data is None or len(data) == 0:
        return ffi.NULL, 0
    return ffi.from_buffer("uint8_t[]", data), len(data)


def view(pointer, length: int, *, writable: bool) -> memoryview:
    """A memoryview of `length` bytes at `pointer`, without copying them."""
    if length == 0:
        return memoryview(b"")
    data = memoryview(ffi.buffer(pointer, length))
    return data if writable else data.toreadonly()


def release(data: memoryview | None) -> None:
    """Releases `data`, so using it afterwards raises, unless something still holds it."""
    if data is None:
        return
    try:
        data.release()
    except BufferError:
        # Something made from it, e.g. a numpy array, still holds it. It must not be used either.
        pass


class Handle:
    """An opaque handle from the C ABI, freed by `destroy` when it is closed."""

    def __init__(self, handle, destroy) -> None:
        self._handle = handle
        self._destroy = destroy

    def _live(self):
        if self._handle is None:
            raise ClosedError(f"{type(self).__name__} is closed")
        return self._handle

    def close(self) -> None:
        """Destroys the handle. Closing it again does nothing."""
        if self._handle is not None:
            handle, self._handle = self._handle, None
            check(self._destroy(handle))

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class ThreadHandle(Handle):
    """A handle that must stay on the thread that created it."""

    def __init__(self, handle, destroy) -> None:
        super().__init__(handle, destroy)
        self._thread = threading.get_native_id()

    def _live(self):
        handle = super()._live()
        if threading.get_native_id() != self._thread:
            raise WrongThreadError(
                f"{type(self).__name__} used from a thread other than the one that created it"
            )
        return handle

    def close(self) -> None:
        if self._handle is not None:
            self._live()
        super().close()


class LockedHandle(Handle):
    """A handle that any thread may use, one call at a time."""

    def __init__(self, handle, destroy) -> None:
        super().__init__(handle, destroy)
        self._lock = threading.Lock()

    def _call(self, function, *args) -> None:
        with self._lock:
            check(function(*args, self._live()))

    def close(self) -> None:
        with self._lock:
            super().close()


def next_of(poll, wait, timeout: float | None):
    """Polls until `poll` returns something, waiting with `wait` in between, for at most `timeout`
    seconds, or without end if it is None. Returns None if the time runs out."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        value = poll()
        if value is not None:
            return value
        if deadline is None:
            wait(_MAX_TIMEOUT_MS / _MS_PER_SECOND)
            continue
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        wait(left)


def create(function, out_type: str, *args):
    """Calls `function`, a `*_create`, with `args`, and returns the handle it creates."""
    out = ffi.new(out_type)
    check(function(*args, out))
    return out[0]
