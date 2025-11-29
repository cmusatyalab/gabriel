"""Integration tests for Gabriel client-server communication."""

import asyncio
import contextlib
import copy
import itertools
import logging
import threading

import pytest
import pytest_asyncio
from gabriel_client.gabriel_client import InputProducer
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.cognitive_engine import Result
from gabriel_server.local_engine import LocalEngine
from gabriel_server.network_engine import engine_runner, server_runner
from prometheus_client import REGISTRY, Counter, Gauge, Histogram, Summary

DEFAULT_NUM_TOKENS = 2
DEFAULT_SERVER_HOST = "localhost"
INPUT_QUEUE_MAXSIZE = 60

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) - "
    "%(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


class Engine(cognitive_engine.Engine, threading.Thread):
    """A simple echo engine that returns the input payload as output."""

    def __init__(self, engine_id, zeromq_address):
        """Initialize the engine and engine runner."""
        super().__init__()
        self.engine_id = engine_id
        self.engine_name = f"Engine-{engine_id}"
        self.zeromq_address = zeromq_address
        self.engine_runner = engine_runner.EngineRunner(
            self,
            self.engine_name,
            self.zeromq_address,
            all_responses_required=True,
            request_retries=3,
        )

        logger.info(f"Engine {engine_id} initialized")

    def handle(self, input_frame):
        """Process a single gabriel_pb2.InputFrame()."""
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
        self.engine_runner.run()

    async def run_async(self):
        """Run the engine runner asynchronously."""
        logger.info(f"Running engine {self.engine_id} asynchronously")
        await self.engine_runner.run_async()

    async def stop(self):
        """Stop the engine runner."""
        await self.engine_runner.stop()


@pytest.fixture(scope="session")
def server_frontend_port_generator():
    """Generate unique server frontend ports for each test."""
    return itertools.count(90999)


@pytest.fixture
def server_frontend_port(server_frontend_port_generator):
    """Get the next available server frontend port."""
    return next(server_frontend_port_generator)


@pytest.fixture(scope="session")
def server_backend_port_generator():
    """Generate unique server backend ports for each test."""
    return itertools.count(55555)


@pytest.fixture
def server_backend_port(server_backend_port_generator):
    """Get the next available server backend port."""
    return next(server_backend_port_generator)


@pytest.fixture
def use_zeromq():
    """Whether to use ZeroMQ for server-client communication."""
    return True


@pytest.fixture
def use_ipc():
    """Whether to use IPC for server-client communication."""
    return False


@pytest.fixture
def num_engines():
    """Return the number of engines to run."""
    return 1


@pytest.fixture(scope="session")
def prometheus_server_port_generator():
    """Generate unique Prometheus ports for each test for server."""
    return itertools.count(8001)


@pytest.fixture
def prometheus_server_port(prometheus_server_port_generator):
    """Get the next available Prometheus port for server."""
    return next(prometheus_server_port_generator)


@pytest.fixture(scope="session")
def prometheus_client_port_generator():
    """Generate unique Prometheus ports for each test for client."""
    return itertools.count(8001)


@pytest.fixture
def prometheus_client_port(prometheus_client_port_generator):
    """Get the next available Prometheus port for client."""
    return next(prometheus_client_port_generator)


@pytest.fixture
def engine_disconnection_timeout():
    """Amount of time before engine is considered disconnected."""
    return 5


@pytest_asyncio.fixture
async def run_server(
    server_frontend_port,
    server_backend_port,
    use_zeromq,
    prometheus_server_port,
    use_ipc,
    engine_disconnection_timeout,
):
    """Run a server with the specified configuration."""
    logger.info(
        f"Starting server: {use_zeromq=} {server_backend_port=}"
        f" {server_frontend_port=} {use_ipc=}"
    )
    if use_ipc:
        client_endpoint = f"/tmp/gabriel_server_{server_frontend_port}.ipc"
    else:
        client_endpoint = server_frontend_port
    server_run = server_runner.ServerRunner(
        client_endpoint=client_endpoint,
        engine_zmq_endpoint=f"tcp://*:{server_backend_port}",
        num_tokens=DEFAULT_NUM_TOKENS,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        timeout=engine_disconnection_timeout,
        use_zeromq=use_zeromq,
        prometheus_port=prometheus_server_port,
        use_ipc=use_ipc,
    )
    task = asyncio.create_task(server_run.run_async())
    task.add_done_callback(lambda t: t.result() if not t.cancelled() else None)
    await asyncio.sleep(0)
    yield server_run
    logger.info("Tearing down server")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    logger.info("Done tearing down server")


@pytest_asyncio.fixture
async def run_engines(run_server, server_backend_port, num_engines):
    """Run engines connected to the server backend port."""
    engines = []
    logger.info(f"Running engines, connecting to {server_backend_port=}!")

    for engine_id in range(num_engines):
        zeromq_address = f"tcp://localhost:{server_backend_port}"
        engine = Engine(engine_id, zeromq_address)
        task = asyncio.create_task(engine.run_async())
        engines.append(task)
        task.add_done_callback(
            lambda t: t.result() if not t.cancelled() else None
        )

    yield engines
    logger.info("Tearing down engines")
    for task in engines:
        task.cancel()
    await asyncio.gather(*engines, return_exceptions=True)
    logger.info("Done tearing down engines")


@pytest.fixture
def target_engines():
    """Obtain the target engines for the input producer."""
    return ["Engine-0"]


@pytest.fixture
def num_inputs_to_send():
    """Obtain the number of inputs to send. If -1, send indefinitely."""
    return -1  # send indefinitely until test ends


@pytest.fixture
def input_producer(target_engines, num_inputs_to_send):
    """Create an InputProducer that sends text frames to the server."""
    logger.info(f"Target engines: {target_engines}")

    inputs_sent = 0

    async def producer() -> gabriel_pb2.InputFrame | None:
        logger.info("Producing input")
        frame = gabriel_pb2.InputFrame()
        frame.payload_type = gabriel_pb2.PayloadType.TEXT
        frame.string_payload = "Hello from client"
        await asyncio.sleep(0.1)

        nonlocal inputs_sent
        nonlocal num_inputs_to_send
        inputs_sent += 1
        if num_inputs_to_send > 0 and inputs_sent > num_inputs_to_send:
            return None
        logger.info(f"Inputs sent: {inputs_sent}")

        return frame

    input_producer = InputProducer(
        producer=producer, target_engine_ids=target_engines
    )
    yield [input_producer]
    input_producer.stop()


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


@pytest.fixture
def response_state():
    """Maintains a dictionary to hold state about responses received."""
    return {"received": False}


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


@pytest.mark.asyncio
async def test_zeromq_client(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
):
    """Test that the zeromq client can connect to a server."""
    response_state.clear()
    response_state["received"] = False

    logger.info("Starting test_zeromq_client")

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    for _ in range(10):
        await asyncio.sleep(0.1)
        if response_state["received"]:
            break
    task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    logger.info("Client task is cancelled")

    assert response_state["received"]

    result = response_state["result"]

    assert result.status.code == gabriel_pb2.StatusCode.SUCCESS
    field_set = result.WhichOneof("payload")
    assert field_set
    assert field_set == "string_result"
    assert result.string_result == "hello"


def get_multiple_engine_consumer(response_state):
    """Create a consumer that counts responses from multiple engines."""

    def multiple_engine_consumer(result):
        logger.info(f"Status is {result.status.code}")
        logger.info(f"Produced by {result.target_engine_id}")
        key = result.target_engine_id
        response_state[key] = response_state.get(key, 0) + 1

    return multiple_engine_consumer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_engines",
    [
        ["Engine-0"],
        ["Engine-0", "Engine-1"],
        ["Engine-0", "Engine-1", "Engine-2"],
    ],
)
@pytest.mark.parametrize("num_engines", [3])
async def test_send_multiple_engines(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that we receive a response from each engine we target."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)

    assert len(response_state) == len(target_engines)


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [["local_engine"]])
async def test_local_server(
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
):
    """Test that we can run a local engine with zeromq."""
    response_state.clear()
    response_state["received"] = False

    engine = LocalEngine(
        lambda: Engine(0, None),
        port=server_frontend_port,
        num_tokens=DEFAULT_NUM_TOKENS,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        use_zeromq=True,
    )
    engine_task = asyncio.create_task(engine.run_async())
    await asyncio.sleep(0)

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    client_task = asyncio.create_task(client.launch_async())

    logger.info("Waiting for response from local engine")

    received = False
    for _ in range(10):
        await asyncio.sleep(0.1)
        if response_state["received"]:
            received = True
            logger.info("Received response from local engine")
            break
    if not received:
        logger.error("Did not receive response from local engine")

    engine_task.cancel()
    client_task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await client_task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    try:
        logger.info("Waiting for engine task to cancel")
        await engine_task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise

    logger.info("Client and engine tasks are cancelled")

    assert response_state["received"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [["local_engine"]])
async def test_ipc_local_engine(
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
):
    """Test that we can run a local engine with ipc."""
    response_state.clear()
    response_state["received"] = False

    engine = LocalEngine(
        lambda: Engine(0, None),
        port=server_frontend_port,
        num_tokens=DEFAULT_NUM_TOKENS,
        input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        use_zeromq=True,
        ipc_path=f"/tmp/gabriel_server_{server_frontend_port}.ipc",
    )
    engine_task = asyncio.create_task(engine.run_async())
    await asyncio.sleep(0)

    client = ZeroMQClient(
        f"ipc:///tmp/gabriel_server_{server_frontend_port}.ipc",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    client_task = asyncio.create_task(client.launch_async())

    logger.info("Waiting for response from local engine")

    received = False
    for _ in range(10):
        await asyncio.sleep(0.1)
        if response_state["received"]:
            received = True
            logger.info("Received response from local engine")
            break
    if not received:
        logger.error("Did not receive response from local engine")

    engine_task.cancel()
    client_task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await client_task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    try:
        logger.info("Waiting for engine task to cancel")
        await engine_task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise

    logger.info("Client and engine tasks are cancelled")

    assert response_state["received"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_engines",
    [
        ["Engine-0"],
        ["Engine-0", "Engine-1"],
        ["Engine-0", "Engine-1", "Engine-2"],
    ],
)
@pytest.mark.parametrize("num_engines", [3])
@pytest.mark.parametrize("use_ipc", [True])
async def test_send_multiple_engines_ipc(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that we receive a response from each engine we target using ipc."""
    response_state.clear()

    client = ZeroMQClient(
        f"ipc:///tmp/gabriel_server_{server_frontend_port}.ipc",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)
    assert len(response_state) == len(target_engines)


@pytest.mark.asyncio
@pytest.mark.parametrize("num_engines", [3])
async def test_change_target_engines(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
):
    """Test that we can change the target engines on the fly."""
    response_state.clear()
    logger.info(f"{server_frontend_port=}")

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert len(response_state) == 1

    input_producer[0].change_target_engines(
        target_engine_ids=["Engine-0", "Engine-1"]
    )

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)

    assert len(response_state) == 2


@pytest.mark.asyncio
async def test_disconnection(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    run_server,
    prometheus_client_port,
):
    """Test that the client can handle server disconnection."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert len(response_state) == 1

    # Simulate server disconnection
    logger.debug("Simulating disconnection")
    server = run_server.get_server()
    await server._close_server_socket()
    await asyncio.sleep(12)
    num_responses = response_state["Engine-0"]

    # Restart server
    await server._recreate_server_socket()
    await asyncio.sleep(2)
    logger.info(f"{response_state=}")
    assert response_state["Engine-0"] > num_responses

    logger.info("Cancelling handler task")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
def metrics_before():
    """Fixture to capture Prometheus metrics before a test runs."""
    yield copy.deepcopy(list(REGISTRY.collect()))


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


@pytest.mark.asyncio
@pytest.mark.parametrize("num_inputs_to_send", [5])
async def test_prometheus_server_metrics(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_server_port,
    metrics_before,
    prometheus_client_port,
):
    """Test that Prometheus metrics are being collected at the server."""
    response_state.clear()
    response_state["received"] = False

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert response_state["received"]

    print(response_state)

    # Check that Prometheus metrics are being collected
    metric_names = [metric.name for metric in metrics_before]
    assert len(metric_names) > 0, "No metrics found in Prometheus registry"
    expected_metrics = [
        "gabriel_engine_processing_latency_seconds",
        "gabriel_producer_queue_length",
        "gabriel_producer_inputs_received",
        "gabriel_engine_inputs_received",
        "gabriel_engine_inputs_processed",
    ]
    for expected_metric in expected_metrics:
        assert expected_metric in metric_names, (
            f"{expected_metric} not found in metrics"
        )

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)
    final_metrics = list(REGISTRY.collect())

    metrics_found = 0
    for metric in final_metrics:
        metrics_found += 1
        if metric.name == "gabriel_engine_processing_latency_seconds":
            found = False
            for sample in metric.samples:
                if (
                    sample.name
                    == "gabriel_engine_processing_latency_seconds_count"
                    and sample.labels.get("engine_id") == "Engine-0"
                ):
                    found = True
                    init_val = (
                        find_value(
                            metrics_before,
                            "gabriel_engine_processing_latency_seconds_count",
                            "engine_id",
                            "Engine-0",
                        )
                        or 0
                    )
                    assert sample.value - init_val == 5
            assert found
        elif metric.name == "gabriel_producer_queue_length":
            assert len(metric.samples) == 0
        elif metric.name == "gabriel_producer_inputs_received":
            found = False
            logger.info(metric)
            for sample in metric.samples:
                if sample.name == "gabriel_producer_inputs_received_total":
                    found = True
                    assert sample.value == 5
            assert found
        elif metric.name == "gabriel_engine_inputs_received":
            found = False
            for sample in metric.samples:
                if sample.name == "gabriel_engine_inputs_received_total":
                    found = True
                    assert sample.value == 5
            assert found
        elif metric.name == "gabriel_engine_inputs_processed":
            found = False
            for sample in metric.samples:
                if sample.name == "gabriel_engine_inputs_processed_total":
                    found = True
                    assert sample.value == 5
            assert found
        else:
            metrics_found -= 1

    assert metrics_found == len(expected_metrics)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_zeromq", [False])
@pytest.mark.parametrize("server_frontend_port", [65535])
async def test_websocket_client(run_engines, input_producer, response_state):
    """Test that the websocket client can connect to a server."""
    response_state.clear()
    response_state["received"] = False

    logger.info("Starting test_websocket_client")

    await asyncio.sleep(0)
    client = WebsocketClient(
        f"ws://{DEFAULT_SERVER_HOST}:65535",
        input_producer,
        get_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    for _ in range(10):
        await asyncio.sleep(0.1)
        if response_state["received"]:
            break
    task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    logger.info("Client task is cancelled")

    assert response_state["received"]


@pytest.mark.asyncio
async def test_stop_producer(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    prometheus_client_port,
):
    """Test that stopping the input producer stops inputs from being sent."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await asyncio.sleep(1)
    assert len(response_state) == 1
    num_responses = response_state["Engine-0"]

    logger.info("Stopping input producer")
    input_producer[0].stop()
    await asyncio.sleep(1)
    assert response_state["Engine-0"] - num_responses <= 1

    logger.info("Resuming input producer")
    input_producer[0].resume()
    await asyncio.sleep(1)
    assert response_state["Engine-0"] - num_responses > 1

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("target_engines", [["invalid_engine"]])
@pytest.mark.asyncio
async def test_invalid_engine(
    run_engines,
    input_producer,
    server_frontend_port,
    prometheus_client_port,
):
    """Test that an invalid engine ID raises an error."""
    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())
    await asyncio.sleep(1)
    exceptions = await asyncio.gather(task, return_exceptions=True)

    assert len(exceptions) == 1
    exception = exceptions[0]
    assert isinstance(exception, Exception)
    assert (
        "Attempt to target engines that are not connected to the server: "
        "{'invalid_engine'}" in str(exception)
    )


@pytest.mark.asyncio
async def test_empty_input_frame(
    run_engines,
    empty_frame_producer,
    server_frontend_port,
    response_state,
    caplog,
    prometheus_client_port,
):
    """Test that an error is raised when an empty frame is produced."""
    response_state.clear()
    response_state["received"] = False

    logger.info("Starting test_zeromq_client")

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        empty_frame_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    for _ in range(10):
        await asyncio.sleep(0.1)
        if response_state["received"]:
            break
    task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    logger.info("Client task is cancelled")

    assert "Input producer produced empty frame" in caplog.text
    assert not response_state["received"]


def bad_handle_none(self, input_frame):
    """An engine handler that returns None."""
    return None


def bad_handle_status(self, input_frame):
    """An engine handler that returns a None status."""
    status = None
    return Result(status, "hello")


@pytest.mark.asyncio
async def test_engine_return_none(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    monkeypatch,
    caplog,
    prometheus_client_port,
):
    """Test for error when an engine returns None."""
    response_state.clear()
    response_state["received"] = False

    monkeypatch.setattr(Engine, "handle", bad_handle_none)

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())
    await asyncio.sleep(1)

    task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    logger.info("Client task is cancelled")

    assert "Incorrect type returned by engine" in caplog.text
    assert (
        "Output status was: ENGINE_ERROR; Incorrect type returned by engine"
    ) in caplog.text
    assert not response_state["received"]


@pytest.mark.asyncio
async def test_engine_return_bad_status(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    monkeypatch,
    caplog,
    prometheus_client_port,
):
    """Test for error when an engine returns an invalid status."""
    response_state.clear()
    response_state["received"] = False

    monkeypatch.setattr(Engine, "handle", bad_handle_status)

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())
    await asyncio.sleep(1)

    task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await task
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelled():
            raise
    logger.info("Client task is cancelled")

    assert "Return status not populated correctly by engine" in caplog.text
    assert (
        "Output status was: ENGINE_ERROR; Return status not populated "
        "correctly by engine"
    ) in caplog.text
    assert not response_state["received"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [[]])
async def test_target_no_engines(
    run_engines,
    input_producer,
    server_frontend_port,
    target_engines,
    caplog,
    prometheus_client_port,
):
    """Test that an exception is thrown if a client targets no engines."""
    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        lambda x: x,
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    await asyncio.sleep(1)

    assert "None targets no engines" in caplog.text

    assert task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_disconnection_timeout", [0.5])
async def test_new_engine_connected(
    run_engines,
    input_producer,
    server_frontend_port,
    response_state,
    server_backend_port,
    caplog,
    prometheus_client_port,
):
    """Test client is updated when new engine is connected to server."""
    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    client_task = asyncio.create_task(client.launch_async())

    await asyncio.sleep(0.1)

    # Launch new engine
    engine_id = 1
    zeromq_address = f"tcp://localhost:{server_backend_port}"
    engine = Engine(engine_id, zeromq_address)
    engine_task = asyncio.create_task(engine.run_async())

    await asyncio.sleep(0.1)

    input_producer[0].change_target_engines(["Engine-1"])
    response_state.clear()
    response_state["received"] = False

    await asyncio.sleep(0.1)

    assert response_state["received"]

    input_producer[0].stop()

    engine_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await engine_task

    await asyncio.sleep(1.5)

    assert "Lost connection to engine worker Engine-1" in caplog.text

    input_producer[0].resume()

    exceptions = await asyncio.gather(client_task, return_exceptions=True)

    assert len(exceptions) == 1
    exception = exceptions[0]
    assert isinstance(exception, Exception)
    assert (
        "Attempt to target engines that are not connected to the server: "
        "{'Engine-1'}" in str(exception)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("num_inputs_to_send", [5])
async def test_prometheus_client_metrics(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_client_port,
    metrics_before,
):
    """Test that Prometheus metrics are being collected at the client."""
    response_state.clear()
    response_state["received"] = False

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
        prometheus_client_port,
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert response_state["received"]

    # Check that Prometheus metrics are being collected
    metric_names = [metric.name for metric in metrics_before]
    assert len(metric_names) > 0, "No metrics found in Prometheus registry"
    expected_metrics = [
        "gabriel_producer_token_count",
        "gabriel_producer_inputs_sent",
        "gabriel_client_input_processing_latency_seconds",
    ]
    for expected_metric in expected_metrics:
        assert expected_metric in metric_names, (
            f"{expected_metric} not found in metrics"
        )

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=2)
    final_metrics = list(REGISTRY.collect())

    print(final_metrics)

    metrics_found = 0
    for metric in final_metrics:
        metrics_found += 1
        if metric.name == "gabriel_producer_inputs_sent":
            found = False
            for sample in metric.samples:
                if sample.name == "gabriel_producer_inputs_sent_total":
                    found = True
                    assert sample.value == 5
            assert found
        elif metric.name == "gabriel_client_input_processing_latency_seconds":
            found = False
            for sample in metric.samples:
                if (
                    sample.name
                    == "gabriel_client_input_processing_latency_seconds_count"
                ):
                    found = True
                    init_val = (
                        find_value(
                            metrics_before,
                            "gabriel_client_input_processing_latency_seconds_count",
                            "producer_id",
                            input_producer[0].producer_id,
                        )
                        or 0
                    )
                    assert init_val == 0
                    assert sample.value == 5
            assert found
        elif metric.name == "gabriel_producer_token_count":
            found = False
            for sample in metric.samples:
                if sample.name == "gabriel_producer_token_count":
                    found = True
                    assert sample.value > 0
            assert found
        else:
            metrics_found -= 1
    assert metrics_found == len(expected_metrics)
