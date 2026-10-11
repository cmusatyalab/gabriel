"""Subscriptions: what happened to a producer's inputs."""

from collections.abc import Iterator

from gabriel._ffi import ThreadHandle, check, create, cstr, ffi, lib, milliseconds, next_of
from gabriel.errors import AgainError
from gabriel.types import Event


class Subscription(ThreadHandle):
    """Reads the events about the inputs of one producer: what happened to each at each engine,
    and lost connections. Only events published after it subscribes are read.

    A subscription must be used and closed on the thread that created it.
    """

    def __init__(self, producer: str) -> None:
        handle = create(lib.gabriel_subscribe_producer, "gabriel_subscription_t **", cstr(producer))
        super().__init__(handle, lib.gabriel_subscription_destroy)

    def wait(self, timeout: float) -> bool:
        """Waits until an event is ready, or `timeout` seconds pass. Returns whether it may be
        ready."""
        try:
            check(lib.gabriel_subscription_wait(self._live(), milliseconds(timeout)))
        except AgainError:
            return False
        return True

    def poll(self) -> Event | None:
        """Returns the next event, or None if none is available."""
        out = ffi.new("gabriel_event_t *")
        try:
            check(lib.gabriel_subscription_poll(self._live(), out))
        except AgainError:
            return None
        return Event._from_c(out)

    def next(self, timeout: float | None = None) -> Event | None:
        """Waits for the next event, for at most `timeout` seconds, or without end if it is None.
        Returns None if the time runs out."""
        return next_of(self.poll, self.wait, timeout)

    def __iter__(self) -> Iterator[Event]:
        """Yields each event as it arrives."""
        while True:
            yield self.next()
