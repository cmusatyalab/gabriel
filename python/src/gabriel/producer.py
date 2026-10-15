"""Producers: publish inputs."""

from gabriel._ffi import ThreadHandle, check, create, cstr, ffi, lib, view
from gabriel.errors import SizeExceededError


class Producer(ThreadHandle):
    """Publishes inputs. An input is written in place into a loaned shared memory buffer (`loan`)
    and then published (`publish`); `send` does both from a bytes-like object.

    A producer must be used and closed on the thread that created it.
    """

    def __init__(self, name: str, *, input_type: str | None = None, max_input_size: int) -> None:
        """Creates the producer `name`, whose inputs are at most `max_input_size` bytes.
        `input_type` names how to read its inputs, e.g. a protobuf message name; engines see it as
        each input's payload type. A client takes its inputs once it adds the producer
        (`Client.add_producer`)."""
        handle = create(
            lib.gabriel_producer_create,
            "gabriel_producer_t **",
            cstr(name),
            cstr(input_type),
            max_input_size,
        )
        super().__init__(handle, lib.gabriel_producer_destroy)
        self._max_input_size = max_input_size
        session = ffi.new("uint64_t *")
        check(lib.gabriel_producer_session(handle, session))
        self._session = session[0]

    @property
    def session(self) -> int:
        """Chosen randomly when the producer started; the ids of its inputs carry it."""
        return self._session

    def loan(self) -> memoryview:
        """Returns a buffer in shared memory for the next input, of the producer's maximum input
        size. Write the input into it, then publish it with `publish`."""
        out = ffi.new("uint8_t **")
        check(lib.gabriel_producer_new_input(self._live(), out))
        return view(out[0], self._max_input_size, writable=True)

    def publish(self, flow: str, length: int) -> int:
        """Publishes the first `length` bytes of the loaned buffer on `flow`, and returns the
        input's sequence number, which the events about the input carry."""
        seq = ffi.new("uint64_t *")
        check(lib.gabriel_producer_publish_input(self._live(), length, cstr(flow), seq))
        return seq[0]

    def send(self, flow: str, data) -> int:
        """Copies `data`, any bytes-like object, into a loaned buffer and publishes it on `flow`."""
        data = memoryview(data).cast("B")
        if len(data) > self._max_input_size:
            raise SizeExceededError(
                f"input of {len(data)} bytes exceeds maximum of {self._max_input_size}"
            )
        buf = self.loan()
        buf[: len(data)] = data
        return self.publish(flow, len(data))
