"""Engines: process one input at a time."""

from collections.abc import Iterator

from gabriel._ffi import (
    ThreadHandle,
    bytes_arg,
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
from gabriel.types import Event, Input, InputId


class Engine(ThreadHandle):
    """Processes one input at a time and reports exactly one outcome for it: `stop`, `pass_` or
    `emit`. A server delivers inputs to it once the server adds it (`Server.add_engine`).

    An engine must be used and closed on the thread that created it.

        with Engine("detector", max_output_size=4096) as engine:
            for input in engine:
                engine.stop(detect(input.data))
    """

    def __init__(self, name: str, *, result_type: str | None = None, max_output_size: int) -> None:
        """Creates the engine `name`. `result_type` names how to read its results, e.g. a protobuf
        message name. An output and its result are together at most `max_output_size` bytes."""
        handle = create(
            lib.gabriel_engine_create,
            "gabriel_engine_t **",
            cstr(name),
            cstr(result_type),
            max_output_size,
        )
        super().__init__(handle, lib.gabriel_engine_destroy)
        self._max_output_size = max_output_size
        self._input: Input | None = None

    def wait(self, timeout: float) -> bool:
        """Waits until an input or an event of a subscription is ready, or `timeout` seconds pass.
        Returns whether one may be ready: a wake-up with neither is possible, so poll in a loop,
        or use `next` for an engine without subscriptions."""
        try:
            check(lib.gabriel_engine_wait(self._live(), milliseconds(timeout)))
        except AgainError:
            return False
        return True

    def poll(self) -> Input | None:
        """Returns the next input, or None if none is available."""
        out = ffi.new("gabriel_input_t *")
        try:
            check(lib.gabriel_engine_poll_input(self._live(), out))
        except AgainError:
            return None
        self._input = Input(
            id=InputId._from_c(out.id),
            payload_type=ffi.string(out.payload_type).decode(),
            data=view(out.buf, out.len, writable=False),
        )
        return self._input

    def next(self, timeout: float | None = None) -> Input | None:
        """Waits for the next input, for at most `timeout` seconds, or without end if it is None.
        Returns None if the time runs out. Events of subscriptions do not end it; an engine with
        subscriptions uses `wait`, `poll` and `poll_event` instead."""
        return next_of(self.poll, self.wait, timeout)

    def subscribe(self, name: str) -> None:
        """Subscribes the engine to the events of the engine `name`, e.g. a detector whose results
        it fuses. Only events published from now on are read. `wait` ends for them too."""
        check(lib.gabriel_engine_subscribe(self._live(), cstr(name)))

    def poll_event(self) -> Event | None:
        """Returns the next event of any subscription, or None if none is available. Its `engine`
        says which subscription it came from."""
        out = ffi.new("gabriel_event_t *")
        try:
            check(lib.gabriel_engine_poll_event(self._live(), out))
        except AgainError:
            return None
        return Event._from_c(out)

    def __iter__(self) -> Iterator[Input]:
        """Yields each input as it arrives. Report its outcome before asking for the next."""
        while True:
            yield self.next()

    def publish_derived(
        self, id: InputId, flow: str, result=b"", *, result_type: str | None = None
    ) -> None:
        """Publishes `result`, which the engine derived from the events it subscribes to, about
        the input `id` in the flow `flow`; e.g. a fusion decision. Pass the `id` and `flow` of the
        event the result is about. `result_type` names how to read it. It answers no offer, so the
        engine can publish it at any time. Its server publishes it as a `DERIVED` event on the
        engine's event service."""
        # Kept alive until the call returns, since the struct only points to it.
        producer = ffi.new("char[]", id.producer.encode())
        c_id = ffi.new("gabriel_input_id_t *", (producer, id.session, id.seq))
        check(
            lib.gabriel_engine_publish_derived(
                self._live(), c_id, cstr(flow), cstr(result_type), *bytes_arg(result)
            )
        )

    def stop(self, result=b"") -> None:
        """Reports that the engine forwards nothing. A discard is a stop with no result."""
        self._report(lib.gabriel_engine_stop, result)

    def pass_(self, result=b"") -> None:
        """Reports that the engine forwards the input unchanged along every outgoing edge."""
        self._report(lib.gabriel_engine_pass, result)

    def _report(self, function, result) -> None:
        handle = self._live()
        self._release_input()
        check(function(handle, *bytes_arg(result)))

    def loan(self) -> memoryview:
        """Returns a buffer in shared memory for a new input, of the engine's maximum output size.
        Write the input into it, then forward it with `emit`."""
        out = ffi.new("uint8_t **")
        check(lib.gabriel_engine_new_output(self._live(), out))
        return view(out[0], self._max_output_size, writable=True)

    def emit(self, length: int, *, output_type: str | None = None, result=b"") -> None:
        """Reports that the engine forwards the first `length` bytes of the loaned buffer as a new
        input along every outgoing edge. `output_type` names how to read it."""
        handle = self._live()
        self._release_input()
        check(lib.gabriel_engine_emit(handle, cstr(output_type), length, *bytes_arg(result)))

    def _release_input(self) -> None:
        """Releases the input's bytes, which the outcome hands back to the server."""
        if self._input is not None:
            release(self._input.data)
            self._input = None
