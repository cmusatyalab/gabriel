"""Values that inputs and events carry."""

import enum
from dataclasses import dataclass

from gabriel._ffi import ffi, lib


@dataclass(frozen=True, slots=True)
class InputId:
    """Identifies one input of a producer."""

    producer: str
    """The producer's name."""
    session: int
    """Chosen randomly when the producer starts, so ids never repeat across restarts."""
    seq: int
    """The input's sequence number, as `Producer.publish` returns it."""

    @classmethod
    def _from_c(cls, view) -> "InputId":
        return cls(ffi.string(view.producer).decode(), view.session, view.seq)


@dataclass(frozen=True, slots=True)
class Input:
    """An input handed to an engine."""

    id: InputId
    payload_type: str
    """How to read `data`; empty if the input has no convention."""
    data: memoryview
    """The input's bytes, in shared memory, read-only. It is released when the engine reports its
    outcome; copy it (`bytes(data)`) to keep it longer. A numpy array made from it with
    `numpy.frombuffer` must not be used after the outcome either."""


@dataclass(frozen=True, slots=True)
class ReadInput:
    """An input a `Reader` read, as its producer published it."""

    id: InputId
    flow: str
    """The flow the producer published the input on."""
    payload_type: str
    """How to read `data`; empty if the input has no convention."""
    data: memoryview
    """The input's bytes, in shared memory, read-only. It is released at the reader's next poll;
    copy it (`bytes(data)`) to keep it longer. A numpy array made from it with `numpy.frombuffer`
    must not be used after that either."""
    lost: int
    """How many inputs of the producer's session were lost right before this one, because the
    reader fell behind."""


class EventKind(enum.IntEnum):
    """What happened to an input."""

    ACCEPTED = lib.GABRIEL_EVENT_KIND_T_ACCEPTED
    """A server received the input and made one offer per target engine."""
    SENT = lib.GABRIEL_EVENT_KIND_T_SENT
    """The offer was sent to the engine; a result may come."""
    RESULT = lib.GABRIEL_EVENT_KIND_T_RESULT
    """The engine finished the input. `Event.outcome` says what it forwarded."""
    SKIPPED = lib.GABRIEL_EVENT_KIND_T_SKIPPED
    """A newer input replaced the offer before it was sent."""
    TIMED_OUT = lib.GABRIEL_EVENT_KIND_T_TIMED_OUT
    """The engine did not answer in time, or was lost."""
    DISCONNECTED = lib.GABRIEL_EVENT_KIND_T_DISCONNECTED
    """The client lost its connection to a server of the flow. Every offer on that server ended
    with it. It names no input."""
    DERIVED = lib.GABRIEL_EVENT_KIND_T_DERIVED
    """A result an engine derived from subscribed events. It answers no offer."""
    LOST = lib.GABRIEL_EVENT_KIND_T_LOST
    """Events were lost before the next one, because the subscriber fell behind."""


class Outcome(enum.IntEnum):
    """What an engine did with an input: what it forwarded along its outgoing edges."""

    DROP = lib.GABRIEL_OUTCOME_T_DROP
    """Nothing; the input ended at the engine."""
    FORWARD = lib.GABRIEL_OUTCOME_T_FORWARD
    """The input, unchanged."""
    REPLACE = lib.GABRIEL_OUTCOME_T_REPLACE
    """A new input, in place of the input."""


def _known(enum_type: type[enum.IntEnum], value: int) -> enum.IntEnum | int:
    """`value` as a member of `enum_type`, or as is if this version does not know it: the ABI may
    add values at the end of an enum."""
    try:
        return enum_type(value)
    except ValueError:
        return value


@dataclass(frozen=True, slots=True)
class Event:
    """What happened to one input at one engine. For `LOST`, only `lost` is set, and `engine` for
    a subscription to an engine; for `DISCONNECTED`, only `flow`, and `engine` for a subscription
    to an engine.

    Times are in nanoseconds on the machine's monotonic clock, which `time.monotonic_ns` reads.
    """

    kind: EventKind | int
    """What happened; a plain int for a kind added in a newer version of the ABI."""
    lost: int
    """How many events were lost, for `LOST`."""
    id: InputId
    engine: str
    """The engine the event is about; empty if none."""
    flow: str
    """The flow the event belongs to; empty if none."""
    outcome: Outcome | int
    """What the engine did with the input, for `RESULT`; a plain int for a value added in a newer
    version of the ABI."""
    payload_type: str
    """How to read `result`; empty if there is no result."""
    result: bytes
    """The engine's result, for `RESULT`."""
    at_ns: int
    """When the event happened."""
    offered_at_ns: int | None
    """When the server made the engine's offer; None if the client made the offer, or for
    `DISCONNECTED`."""
    received_at_ns: int | None
    """When the client received the event from the remote server, on this machine's clock;
    `at_ns` and `offered_at_ns` are then on the remote server's clock. None for other events."""

    @classmethod
    def _from_c(cls, view) -> "Event":
        return cls(
            kind=_known(EventKind, view.kind),
            lost=view.lost,
            id=InputId._from_c(view.id),
            engine=ffi.string(view.engine).decode(),
            flow=ffi.string(view.flow).decode(),
            outcome=_known(Outcome, view.outcome),
            payload_type=ffi.string(view.payload_type).decode(),
            result=bytes(ffi.buffer(view.result, view.result_len)) if view.result_len else b"",
            at_ns=view.at_ns,
            offered_at_ns=view.offered_at_ns or None,
            received_at_ns=view.received_at_ns or None,
        )
