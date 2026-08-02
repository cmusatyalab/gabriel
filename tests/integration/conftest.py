"""Shared fixtures for the Gabriel integration test suite."""

import asyncio
import logging

import pytest
import pytest_asyncio
from gabriel_client.gabriel_client import InputProducer
from gabriel_protocol.v1 import gabriel_pb2
from gabriel_server.network_engine import server_runner
from gabriel_server.network_engine.server_runner import Transport
from helpers import (
    DEFAULT_NUM_TOKENS,
    INPUT_QUEUE_MAXSIZE,
    Engine,
    free_port_generator,
    wait_until,
)
from prometheus_client import REGISTRY, Counter, Gauge, Histogram, Summary

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) - "
    "%(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# --- Ports -------------------------------------------------------------------
#
# Each of these hands out a fresh, unused TCP port per test (scoped to a
# session-level generator so ports are never reused within a run, which matters
# both for test isolation and for safety under pytest-xdist).


@pytest.fixture(scope="session")
def server_frontend_port_generator():
    """Generate unique server frontend ports for each test."""
    return free_port_generator()


@pytest.fixture
def server_frontend_port(server_frontend_port_generator):
    """Get the next available server frontend port."""
    return next(server_frontend_port_generator)


@pytest.fixture(scope="session")
def server_backend_port_generator():
    """Generate unique server backend ports for each test."""
    return free_port_generator()


@pytest.fixture
def server_backend_port(server_backend_port_generator):
    """Get the next available server backend port."""
    return next(server_backend_port_generator)


@pytest.fixture(scope="session")
def prometheus_server_port_generator():
    """Generate unique Prometheus ports for each test for server."""
    return free_port_generator()


@pytest.fixture
def prometheus_server_port(prometheus_server_port_generator):
    """Get the next available Prometheus port for server."""
    return next(prometheus_server_port_generator)


@pytest.fixture(scope="session")
def prometheus_client_port_generator():
    """Generate unique Prometheus ports for each test for client."""
    return free_port_generator()


@pytest.fixture
def prometheus_client_port(prometheus_client_port_generator):
    """Get the next available Prometheus port for client."""
    return next(prometheus_client_port_generator)


# --- Server ------------------------------------------------------------------


@pytest.fixture
def client_ipc_path(tmp_path):
    """Unix domain socket path for server-client IPC."""
    return tmp_path / "gabriel_server.ipc"


@pytest.fixture
def engine_ipc_path(tmp_path):
    """Unix domain socket path for server-engine IPC."""
    return tmp_path / "gabriel_engine.ipc"


@pytest.fixture
def transport():
    """Which transport to use for server-client communication."""
    return Transport.ZEROMQ


@pytest.fixture
def use_client_ipc():
    """Whether to use IPC for server-client communication."""
    return False


@pytest.fixture
def use_engine_ipc():
    """Whether to use a Unix domain socket for server-engine communication."""
    return False


@pytest.fixture
def num_tokens():
    """Number of tokens to use for the input producer."""
    return DEFAULT_NUM_TOKENS


@pytest_asyncio.fixture
async def run_server(
    server_frontend_port,
    server_backend_port,
    transport,
    prometheus_server_port,
    use_client_ipc,
    use_engine_ipc,
    client_ipc_path,
    engine_ipc_path,
):
    """Run a server with the specified configuration."""
    logger.info(
        f"Starting server: {transport=} {server_backend_port=}"
        f" {server_frontend_port=} {use_client_ipc=} {use_engine_ipc=}"
    )
    if use_client_ipc:
        client_endpoint = str(client_ipc_path)
    else:
        client_endpoint = server_frontend_port
    if use_engine_ipc:
        engine_endpoint = str(engine_ipc_path)
    else:
        engine_endpoint = server_backend_port
    server_run = server_runner.ServerRunner(
        client_endpoint=client_endpoint,
        engine_endpoint=engine_endpoint,
        num_tokens=DEFAULT_NUM_TOKENS,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        client_transport=transport,
        prometheus_port=prometheus_server_port,
        use_client_ipc=use_client_ipc,
        use_engine_ipc=use_engine_ipc,
    )
    task = asyncio.create_task(server_run.run_async())
    task.add_done_callback(lambda t: t.result() if not t.cancelled() else None)
    for _ in range(100):
        await asyncio.sleep(0.05)
        server = getattr(server_run, "server", None)
        if server is not None and server.is_running():
            break
    yield server_run
    logger.info("Tearing down server")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    logger.info("Done tearing down server")


# --- Engines -----------------------------------------------------------------


@pytest.fixture
def num_engines():
    """Return the number of engines to run."""
    return 1


@pytest.fixture
def handle_method():
    """An engine handle method."""
    return None


@pytest.fixture
def engine_ids():
    """An engine handle method."""
    return None


@pytest.fixture
def run_engines_threaded():
    """Run engines in a different thread."""
    return False


@pytest_asyncio.fixture
async def run_engines(
    run_server,
    server_backend_port,
    use_engine_ipc,
    num_engines,
    handle_method,
    engine_ids,
    run_engines_threaded,
    engine_ipc_path,
):
    """Run engines connected to the server backend port.

    Waits for every expected engine name to actually show up as registered
    server-side before yielding, since a test's input producer can start
    sending (and targeting an engine) almost immediately once a client
    connects, and if that engine hasn't finished its registration yet, the
    client raises a fatal "not connected" error.
    """
    engines = []
    engine_tasks = []
    expected_names = set()
    logger.info(f"Running engines, connecting to {server_backend_port=}!")

    for i in range(num_engines):
        if use_engine_ipc:
            engine_address = f"unix://{engine_ipc_path}"
        else:
            engine_address = f"localhost:{server_backend_port}"
        engine_id = engine_ids[i] if engine_ids else i
        engine = Engine(engine_id, engine_address, handle_method)
        expected_names.add(engine.engine_name)
        engines.append(engine)
        if run_engines_threaded:
            engine.start()
        else:
            task = asyncio.create_task(engine.run_async())
            task.add_done_callback(
                lambda t: t.result() if not t.cancelled() else None
            )
            engine_tasks.append(task)

    await wait_until(
        lambda: expected_names <= run_server.server._engine_ids, timeout=10
    )

    yield engines
    if run_engines_threaded:
        logger.info("Tearing down threaded engines")
        for engine in engines:
            engine.engine_runner.stop_event.set()
        for engine in engines:
            engine.join()
        return
    logger.info("Tearing down engines")
    for task in engine_tasks:
        task.cancel()
    await asyncio.gather(*engine_tasks, return_exceptions=True)
    logger.info("Done tearing down engines")


# --- Input producers ---------------------------------------------------------


@pytest.fixture
def target_engines():
    """Obtain the target engines for the input producer."""
    return ["Engine-0"]


@pytest.fixture
def num_inputs_to_send():
    """Obtain the number of inputs to send. If -1, send indefinitely."""
    return -1  # send indefinitely until test ends


def _make_text_producer(target_engines, num_inputs_to_send):
    """Build an async producer callable that emits text frames."""
    inputs_sent = 0

    async def producer() -> gabriel_pb2.InputFrame | None:
        logger.info("Producing input")
        frame = gabriel_pb2.InputFrame()
        frame.payload_type = gabriel_pb2.PayloadType.TEXT
        frame.string_payload = "Hello from client"
        await asyncio.sleep(0.1)

        nonlocal inputs_sent
        inputs_sent += 1
        if num_inputs_to_send > 0 and inputs_sent > num_inputs_to_send:
            return None
        logger.info(f"Inputs sent: {inputs_sent}")

        return frame

    return producer


@pytest.fixture
def input_producer(target_engines, num_inputs_to_send):
    """Create an InputProducer that sends text frames to the server."""
    logger.info(f"Target engines: {target_engines}")
    producer = _make_text_producer(target_engines, num_inputs_to_send)
    input_producer = InputProducer(
        producer=producer, target_engine_ids=target_engines
    )
    yield [input_producer]
    input_producer.stop()


@pytest.fixture
def multiple_input_producers(target_engines, num_inputs_to_send):
    """Create three InputProducers that share a single producer callback."""
    logger.info(f"Target engines: {target_engines}")
    producer = _make_text_producer(target_engines, num_inputs_to_send)
    producers = [
        InputProducer(producer=producer, target_engine_ids=target_engines)
        for _ in range(3)
    ]
    yield producers
    for p in producers:
        p.stop()


@pytest.fixture
def empty_frame_producer(target_engines, num_inputs_to_send):
    """A producer that does not set fields in the frame it returns."""

    async def producer():
        logger.info("Producing bad input")
        frame = gabriel_pb2.InputFrame()
        await asyncio.sleep(0.1)

        return frame

    input_producer = InputProducer(
        producer=producer, target_engine_ids=target_engines
    )
    yield [input_producer]
    input_producer.stop()


# --- Response tracking / metrics ---------------------------------------------


@pytest.fixture
def response_state():
    """Maintains a dictionary to hold state about responses received."""
    return {"received": False}


@pytest.fixture(autouse=True)
def reset_prometheus_metrics():
    """Reset the state (samples) of all custom metrics between tests.

    Does not unregister metrics.
    """
    yield  # Run the test first
    for collector in list(REGISTRY._collector_to_names.keys()):
        # Only touch application-defined metrics
        if collector.__class__ in (Counter, Gauge, Summary, Histogram):
            try:
                collector._metrics.clear()  # Clear all labeled samples
                if hasattr(collector, "_value"):
                    collector._value.set(0)  # Reset non-labeled metric
            except Exception:
                pass  # Ignore system collectors like process_*, python_gc_*
