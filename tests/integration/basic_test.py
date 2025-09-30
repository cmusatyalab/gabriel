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
from gabriel_server.local_engine import LocalEngine
from gabriel_server.network_engine import engine_runner, server_runner
from prometheus_client import REGISTRY

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
        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)

        result = gabriel_pb2.ResultWrapper.Result()
        result.payload_type = gabriel_pb2.PayloadType.IMAGE
        result.payload = input_frame.payloads[0]
        result_wrapper.results.append(result)

        return result_wrapper

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
def prometheus_port_generator():
    """Generate unique Prometheus ports for each test."""
    return itertools.count(8001)


@pytest.fixture
def prometheus_port(prometheus_port_generator):
    """Get the next available Prometheus port."""
    return next(prometheus_port_generator)


@pytest_asyncio.fixture
async def run_server(
    server_frontend_port,
    server_backend_port,
    use_zeromq,
    prometheus_port,
    use_ipc,
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
        use_zeromq=use_zeromq,
        prometheus_port=prometheus_port,
        use_ipc=use_ipc,
    )
    task = asyncio.create_task(server_run.run_async())
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
        engines.append(asyncio.create_task(engine.run_async()))

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
        frame.payloads.append(b"Hello from client")
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
def response_state():
    """Maintains a dictionary to hold state about responses received."""
    return {"received": False}


def get_consumer(response_state):
    """Create a consumer that sets response_state['received'] to True."""

    def consumer(result_wrapper):
        logger.info(f"Status is {result_wrapper.status}")
        logger.info(f"Produced by {result_wrapper.result_producer_name.value}")
        response_state["received"] = True

    return consumer


@pytest.mark.asyncio
async def test_zeromq_client(
    run_engines, input_producer, server_frontend_port, response_state
):
    """Test that the zeromq client can connect to a server."""
    response_state.clear()
    response_state["received"] = False

    logger.info("Starting test_zeromq_client")

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
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


def get_multiple_engine_consumer(response_state):
    """Create a consumer that counts responses from multiple engines."""

    def multiple_engine_consumer(result_wrapper):
        logger.info(f"Status is {result_wrapper.status}")
        logger.info(f"Produced by {result_wrapper.result_producer_name.value}")
        key = result_wrapper.result_producer_name.value
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
):
    """Test that we receive a response from each engine we target."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)

    assert len(response_state) == len(target_engines)


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [["local_engine"]])
async def test_local_server(
    input_producer, server_frontend_port, response_state
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
    input_producer, server_frontend_port, response_state
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
):
    """Test that we receive a response from each engine we target using ipc."""
    response_state.clear()

    client = ZeroMQClient(
        f"ipc:///tmp/gabriel_server_{server_frontend_port}.ipc",
        input_producer,
        get_multiple_engine_consumer(response_state),
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
):
    """Test that we can change the target engines on the fly."""
    response_state.clear()
    logger.info(f"{server_frontend_port=}")

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert len(response_state) == 1

    input_producer[0].stop()
    input_producer[0].start(target_engine_ids=["Engine-0", "Engine-1"])

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
):
    """Test that the client can handle server disconnection."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=1)

    assert len(response_state) == 1

    # Simulate server disconnection
    logger.info("Stopping server client handler to simulate disconnection")
    server = run_server.get_server()
    await server._stop_client_handler()
    await asyncio.sleep(15)
    num_responses = response_state["Engine-0"]

    # Restart server
    server_task = asyncio.create_task(server._restart_client_handler())
    await asyncio.sleep(1)
    logger.info(f"{response_state=}")
    assert response_state["Engine-0"] > num_responses

    server_task.cancel()
    task.cancel()
    await asyncio.gather(server_task, task, return_exceptions=True)


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
async def test_prometheus_metrics(
    input_producer,
    server_frontend_port,
    target_engines,
    run_engines,
    response_state,
    prometheus_port,
    metrics_before,
):
    """Test that Prometheus metrics are being collected."""
    response_state.clear()
    response_state["received"] = False

    # Check that Prometheus metrics are being collected
    metric_names = [metric.name for metric in metrics_before]
    assert len(metric_names) > 0, "No metrics found in Prometheus registry"
    expected_metrics = [
        "engine_processing_latency_seconds",
        "source_queue_length",
        "input_inter_arrival_time_seconds",
    ]
    for expected_metric in expected_metrics:
        assert expected_metric in metric_names, (
            f"{expected_metric} not found in metrics"
        )

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1)

    assert response_state["received"]

    final_metrics = list(REGISTRY.collect())

    for metric in final_metrics:
        if metric.name == "engine_processing_latency_seconds":
            for sample in metric.samples:
                if (
                    sample.name == "engine_processing_latency_seconds_count"
                    and sample.labels.get("engine_id") == "Engine-0"
                ):
                    init_val = (
                        find_value(
                            metrics_before,
                            "engine_processing_latency_seconds_count",
                            "engine_id",
                            "Engine-0",
                        )
                        or 0
                    )
                    assert sample.value - init_val == 5
        elif metric.name == "source_queue_length":
            for sample in metric.samples:
                if sample.name == "source_queue_length":
                    assert sample.value == 0
        elif metric.name == "input_inter_arrival_time_seconds":
            found = False
            for sample in metric.samples:
                if (
                    sample.name == "input_inter_arrival_time_seconds_count"
                    and sample.labels.get("source_id")
                    == input_producer[0].source_id
                ):
                    found = True
                    init_val = (
                        find_value(
                            metrics_before,
                            "input_inter_arrival_time_seconds_count",
                            "source_id",
                            input_producer[0].source_id,
                        )
                        or 0
                    )
                    assert (
                        sample.value - init_val == 4
                    )  # at least 4 intervals for 5 inputs
            assert found


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
):
    """Test that stopping the input producer stops inputs from being sent."""
    response_state.clear()

    client = ZeroMQClient(
        f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
        input_producer,
        get_multiple_engine_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    await asyncio.sleep(1)
    assert len(response_state) == 1
    num_responses = response_state["Engine-0"]

    input_producer[0].stop()
    await asyncio.sleep(1)
    assert response_state["Engine-0"] - num_responses <= 1

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# @pytest.mark.parametrize("target_engines", [["invalid_engine"]])
# @pytest.mark.asyncio
# async def test_invalid_engine(
#     run_engines, input_producer, server_frontend_port
# ):
#     """Test that an invalid engine ID raises an error."""
#     client = ZeroMQClient(
#         f"tcp://{DEFAULT_SERVER_HOST}:{server_frontend_port}",
#         input_producer,
#         lambda x: x,
#     )
#     task = asyncio.create_task(client.launch_async())

#     with pytest.raises(Exception, match="Server dropped frame"):
#         await asyncio.sleep(1)
#         await task

#     task.cancel()
#     await asyncio.gather(task, return_exceptions=True)
