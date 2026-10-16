"""Readers: the inputs a producer publishes."""

from collections.abc import Iterator

from gabriel._ffi import (
    ThreadHandle,
    check,
    create,
    cstr,
    ffi,
    lib,
    milliseconds,
    next_of,
    release,
    view,
)
from gabriel.errors import AgainError
from gabriel.types import InputId, ReadInput


class Reader(ThreadHandle):
    """Reads the inputs that one producer publishes, in order, alongside the client that takes
    them. Unlike a subscription to the producer, which reads what happened to its inputs, it reads
    the inputs themselves. A reader that falls behind loses the oldest, and learns how many from
    `ReadInput.lost`. Only inputs published after it is created are read.

    A reader must be used and closed on the thread that created it.

        with Reader("camera") as reader:
            for input in reader:
                record(input.data)
    """

    def __init__(self, name: str) -> None:
        """Reads the inputs of the producer `name`. The producer need not exist yet."""
        handle = create(lib.gabriel_reader_create, "gabriel_reader_t **", cstr(name))
        super().__init__(handle, lib.gabriel_reader_destroy)
        self._input: ReadInput | None = None

    def wait(self, timeout: float) -> bool:
        """Waits until an input is ready, or `timeout` seconds pass. Returns whether it may be
        ready."""
        try:
            check(lib.gabriel_reader_wait(self._live(), milliseconds(timeout)))
        except AgainError:
            return False
        return True

    def poll(self) -> ReadInput | None:
        """Returns the next input, or None if none is available. Its `data` is released at the
        next poll."""
        handle = self._live()
        self._release_input()
        out = ffi.new("gabriel_read_input_t *")
        try:
            check(lib.gabriel_reader_poll(handle, out))
        except AgainError:
            return None
        self._input = ReadInput(
            id=InputId._from_c(out.id),
            flow=ffi.string(out.flow).decode(),
            payload_type=ffi.string(out.payload_type).decode(),
            data=view(out.buf, out.len, writable=False),
            lost=out.lost,
        )
        return self._input

    def next(self, timeout: float | None = None) -> ReadInput | None:
        """Waits for the next input, for at most `timeout` seconds, or without end if it is None.
        Returns None if the time runs out."""
        return next_of(self.poll, self.wait, timeout)

    def __iter__(self) -> Iterator[ReadInput]:
        """Yields each input as it arrives. Each one's `data` is released when the next is
        asked for."""
        while True:
            yield self.next()

    def close(self) -> None:
        if self._handle is not None:
            self._live()
            self._release_input()
        super().close()

    def _release_input(self) -> None:
        """Releases the last input's bytes, which the next poll hands back to shared memory."""
        if self._input is not None:
            release(self._input.data)
            self._input = None
