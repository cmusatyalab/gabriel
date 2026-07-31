"""Shared constants, helpers, and test doubles for the integration suite.

Plain (non-fixture) building blocks live here so they can be imported directly
by whichever test module needs them, without going through conftest.py (which
is reserved for pytest fixtures).
"""

import asyncio
import logging
import os
import random
import socket
import threading
from collections.abc import Awaitable, Callable

from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.cognitive_engine import Result
from gabriel_server.network_engine import engine_runner

DEFAULT_NUM_TOKENS = 5
DEFAULT_SERVER_HOST = "localhost"
INPUT_QUEUE_MAXSIZE = 60

logger = logging.getLogger(__name__)

# Binding to port 0 hands back whatever port the OS currently considers free,
# but immediately releases it - two pytest-xdist workers doing this at nearly
# the same time can be handed the *same* port, and only find out one of them
# loses when the real server tries to bind it later. Confining each worker to
# its own disjoint range removes that race for the common case (collisions
# between our own workers) without touching how the actual servers bind.
# _PORT_RANGE_SIZE ports per worker comfortably covers the handful of ports any
# one test needs at once.
_PORT_RANGE_BASE = 20_000
_PORT_RANGE_SIZE = 1_000
_MAX_ATTEMPTS = 100

# Ports already handed out by this worker but not yet necessarily bound by
# their real user, so a second pick in the same process doesn't reissue one
# still awaiting use.
_issued_ports: set[int] = set()


def _worker_port_range() -> range:
    """This pytest-xdist worker's disjoint slice of the port space.

    PYTEST_XDIST_WORKER is e.g. "gw0", "gw1", ...; absent when not running
    under xdist (or in the "master"/controller process), which just gets
    slice 0.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    index = int(worker.removeprefix("gw")) if worker.startswith("gw") else 0
    start = _PORT_RANGE_BASE + index * _PORT_RANGE_SIZE
    return range(start, start + _PORT_RANGE_SIZE)


def get_free_port() -> int:
    """Pick an unused TCP port from this worker's own port range."""
    candidates = _worker_port_range()
    for _ in range(_MAX_ATTEMPTS):
        port = random.choice(candidates)
        if port in _issued_ports:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("localhost", port))
            except OSError:
                continue
        _issued_ports.add(port)
        return port
    raise RuntimeError(
        f"could not find a free port in {candidates} after "
        f"{_MAX_ATTEMPTS} attempts"
    )


def free_port_generator():
    """An infinite generator of unique, unused TCP ports."""
    while True:
        yield get_free_port()


async def wait_until(
    condition: Callable[[], bool],
    timeout: float = 3.0,
    interval: float = 0.1,
) -> bool:
    """Poll `condition()` until it's truthy, or `timeout` seconds elapse."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if condition():
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(interval)


async def cancel_and_wait(task: "asyncio.Task[Awaitable]") -> None:
    """Cancel `task` and wait for it to finish, swallowing cancellation."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelled():
            raise


class Engine(cognitive_engine.Engine, threading.Thread):
    """A simple echo engine that returns the input payload as output."""

    def __init__(
        self,
        engine_id,
        engine_address,
        handle_method=None,
        tls_ca_cert=None,
        tls_client_cert=None,
        tls_client_key=None,
    ):
        """Initialize the engine and engine runner."""
        super().__init__(daemon=True)
        self.engine_id = engine_id
        self.engine_name = f"Engine-{engine_id}"
        self.engine_address = engine_address
        self.engine_runner = engine_runner.EngineRunner(
            self,
            self.engine_name,
            self.engine_address,
            all_responses_required=True,
            request_retries=3,
            tls_ca_cert=tls_ca_cert,
            tls_client_cert=tls_client_cert,
            tls_client_key=tls_client_key,
        )
        self.handle_method = handle_method

        logger.info(f"Engine {engine_id} initialized")

    def handle(self, input_frame, client_info):
        """Process a single gabriel_pb2.InputFrame()."""
        if self.handle_method:
            return self.handle_method(input_frame, client_info)
        logger.info(f"Engine {self.engine_id} received frame")

        assert (
            input_frame.payload_type
            != gabriel_pb2.PayloadType.PAYLOAD_TYPE_UNSPECIFIED
        )

        status = gabriel_pb2.Status()
        status.code = gabriel_pb2.StatusCode.SUCCESS

        return Result(status, "hello")

    def run(self):
        """Run the engine runner."""
        logger.info(f"Running engine {self.engine_id} in a new thread")
        self.engine_runner.run()

    async def run_async(self):
        """Run the engine runner asynchronously."""
        logger.info(f"Running engine {self.engine_id} asynchronously")
        await self.engine_runner.run_async()

    async def stop(self):
        """Stop the engine runner."""
        await self.engine_runner.stop()


def get_consumer(response_state):
    """Create a consumer that sets response_state['received'] to True."""

    def consumer(result):
        assert result.HasField("status")
        assert len(result.target_engine_id) > 0
        assert result.frame_id > 0
        logger.info("Received result")
        logger.info(f"Status is {result.status.code}")
        logger.info(f"Produced by {result.target_engine_id}")
        response_state["received"] = True
        response_state["result"] = result

    return consumer


def get_multiple_engine_consumer(response_state):
    """Create a consumer that counts responses from multiple engines."""

    def multiple_engine_consumer(result):
        logger.info(f"Status is {result.status.code}")
        logger.info(f"Produced by {result.target_engine_id}")
        key = result.target_engine_id
        response_state[key] = response_state.get(key, 0) + 1

    return multiple_engine_consumer


def find_value(metrics, metric_name, label_name=None, label_value=None):
    """Find the value of a metric with an optional label filter."""
    return next(
        (
            sample.value
            for metric in metrics
            for sample in metric.samples
            if sample.name == metric_name
            and (
                label_name is None
                or sample.labels.get(label_name) == label_value
            )
        ),
        None,
    )
