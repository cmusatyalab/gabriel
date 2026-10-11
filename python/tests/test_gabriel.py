import itertools
import os
import signal
import threading
import time

import numpy as np
import pytest

import gabriel

MAX_INPUT_SIZE = 64
MAX_OUTPUT_SIZE = 64
FLOW = "flow"
# Far longer than anything here takes, so a test fails rather than hangs.
WAIT = 5.0
# How long a test lets a wait run before interrupting it.
INTERRUPT_AFTER = 0.2

_names = itertools.count()


def unique(base: str) -> str:
    """A name no other test uses, in this process or another."""
    return f"{base}-{os.getpid()}-{next(_names)}"


def chain(producer: str, engine: str) -> str:
    """A flow where `producer` feeds `engine`."""
    return f"""
[[producers]]
name = "{producer}"
tokens = 1

[[engines]]
name = "{engine}"
location = "local"

[[edges]]
from = "{producer}"
to = "{engine}"
timeout = "5s"
"""


class Fixture:
    """A local server serving one engine, a client of it, and a producer the client takes inputs
    from."""

    def __init__(self) -> None:
        self.producer_name, self.engine_name = unique("camera"), unique("detector")
        server_name = unique("server")
        self.server = gabriel.Server(server_name)
        self.engine = gabriel.Engine(self.engine_name, max_output_size=MAX_OUTPUT_SIZE)
        self.server.add_engine(self.engine_name)
        self.client = gabriel.Client(unique("client"), server_name)
        self.producer = gabriel.Producer(self.producer_name, max_input_size=MAX_INPUT_SIZE)
        self.client.add_producer(self.producer_name)

    def add_flow(self) -> None:
        self.client.add_flow(FLOW, chain(self.producer_name, self.engine_name))

    def close(self) -> None:
        for handle in (self.producer, self.client, self.engine, self.server):
            handle.close()


@pytest.fixture
def fixture():
    f = Fixture()
    yield f
    f.close()


def test_input_reaches_engine_and_result_reaches_app(fixture):
    with gabriel.Subscription(fixture.producer_name) as events:
        fixture.add_flow()

        seq = fixture.producer.send(FLOW, b"frame")
        input = fixture.engine.next(WAIT)
        assert bytes(input.data) == b"frame"
        assert input.id.producer == fixture.producer_name
        assert input.id.seq == seq
        fixture.engine.pass_(b"cat")

        deadline = time.monotonic() + WAIT
        while (event := events.next(deadline - time.monotonic())).kind != gabriel.EventKind.RESULT:
            pass
        assert event.engine == fixture.engine_name
        assert event.id.seq == seq
        assert event.forward == gabriel.Forward.PASS
        assert event.result == b"cat"
        assert 0 < event.at_ns <= time.monotonic_ns()


def test_loaned_input_is_published_in_place(fixture):
    fixture.add_flow()

    buf = fixture.producer.loan()
    assert len(buf) == MAX_INPUT_SIZE
    buf[:8] = b"in place"
    fixture.producer.publish(8, FLOW)

    assert bytes(fixture.engine.next(WAIT).data) == b"in place"


def test_numpy_reads_an_input_without_copying_it(fixture):
    fixture.add_flow()
    fixture.producer.send(FLOW, np.arange(8, dtype=np.uint8))

    input = fixture.engine.next(WAIT)
    frame = np.frombuffer(input.data, dtype=np.uint8)

    assert frame.tolist() == list(range(8))
    assert not frame.flags.owndata


def test_input_cannot_be_read_after_its_outcome(fixture):
    fixture.add_flow()
    fixture.producer.send(FLOW, b"frame")
    input = fixture.engine.next(WAIT)

    fixture.engine.stop()

    with pytest.raises(ValueError):
        bytes(input.data)


def test_error_says_what_failed(fixture):
    with pytest.raises(gabriel.InvalidArgumentError, match="invalid argument: "):
        fixture.client.add_flow(FLOW, "[[edges]")


def test_oversized_input_is_refused(fixture):
    with pytest.raises(gabriel.SizeExceededError):
        fixture.producer.send(FLOW, bytes(MAX_INPUT_SIZE + 1))


def test_engine_without_input_times_out(fixture):
    assert fixture.engine.next(0.01) is None
    assert fixture.engine.poll() is None


def test_ctrl_c_interrupts_a_waiting_engine(fixture):
    timer = threading.Timer(INTERRUPT_AFTER, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    start = time.monotonic()

    with pytest.raises(KeyboardInterrupt):
        fixture.engine.next()

    assert time.monotonic() - start < WAIT
    timer.join()


def test_engine_on_a_thread_of_its_own_gets_inputs():
    server_name, engine_name, producer_name = unique("server"), unique("upper"), unique("app")
    ready, results = threading.Event(), []

    def serve():
        with gabriel.Engine(engine_name, max_output_size=MAX_OUTPUT_SIZE) as engine:
            ready.set()
            input = engine.next(WAIT)
            results.append(bytes(input.data))
            engine.stop()

    thread = threading.Thread(target=serve)
    with gabriel.Server(server_name) as server:
        thread.start()
        assert ready.wait(WAIT)
        server.add_engine(engine_name)
        with (
            gabriel.Producer(producer_name, max_input_size=MAX_INPUT_SIZE) as producer,
            gabriel.Client(unique("client"), server_name) as client,
        ):
            client.add_producer(producer_name)
            client.add_flow(FLOW, chain(producer_name, engine_name))
            producer.send(FLOW, b"frame")
            thread.join(WAIT)

    assert results == [b"frame"]


def test_engine_used_from_another_thread_fails(fixture):
    errors = []
    thread = threading.Thread(target=lambda: errors.append(_raised(fixture.engine.poll)))
    thread.start()
    thread.join()

    assert isinstance(errors[0], gabriel.WrongThreadError)


def _raised(function) -> Exception | None:
    try:
        function()
    except gabriel.GabrielError as e:
        return e
    return None


def test_closed_handle_fails():
    producer = gabriel.Producer(unique("camera"), max_input_size=MAX_INPUT_SIZE)
    producer.close()
    producer.close()

    with pytest.raises(gabriel.ClosedError):
        producer.loan()


def test_remote_server_gives_its_port_and_certificate():
    with gabriel.Server(unique("server"), "127.0.0.1:0") as server:
        assert server.remote_port > 0
        assert len(server.certificate) > 0
