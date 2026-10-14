"""Subscriptions: what happened to a producer's inputs, or at an engine."""

from collections.abc import Iterator

from gabriel._ffi import ThreadHandle, check, create, cstr, ffi, lib, milliseconds, next_of
from gabriel.errors import AgainError
from gabriel.types import Event


class Subscription(ThreadHandle):
    """Reads the events of one event service. Only events published after it subscribes are read.
    Create one with `Subscription.producer` or `Subscription.engine`.

    A subscription must be used and closed on the thread that created it.
    """

    def __init__(self, subscribe, name: str) -> None:
        handle = create(subscribe, "gabriel_subscription_t **", cstr(name))
        super().__init__(handle, lib.gabriel_subscription_destroy)

    @classmethod
    def producer(cls, name: str) -> "Subscription":
        """Subscribes to the events about the inputs of the producer `name`: what happened to each
        at each engine, and lost connections."""
        return cls(lib.gabriel_subscribe_producer, name)

    @classmethod
    def engine(cls, name: str) -> "Subscription":
        """Subscribes to the events of the engine `name`: what happened to each input at that
        engine."""
        return cls(lib.gabriel_subscribe_engine, name)

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
