import asyncio
import itertools
import logging
import pytest
import pytest_asyncio
import threading

from gabriel_client.zeromq_client import InputProducer

# from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2
from gabriel_server.network_engine import server_runner
from gabriel_server.network_engine import engine_runner
from gabriel_server import cognitive_engine
from gabriel_server.local_engine import LocalEngine

DEFAULT_NUM_TOKENS = 2
DEFAULT_SERVER_HOST = "localhost"
INPUT_QUEUE_MAXSIZE = 60

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# @pytest.fixture(scope="session")
# def event_loop():
#    try:
#        loop = asyncio.get_running_loop()
#    except RuntimeError:
#        loop = asyncio.new_event_loop()
#    yield loop
#    loop.close()


class Engine(cognitive_engine.Engine, threading.Thread):
    def __init__(self, engine_id, zeromq_address):
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
        logger.info(f"Engine {self.engine_id} received frame")
        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)

        result = gabriel_pb2.ResultWrapper.Result()
        result.payload_type = gabriel_pb2.PayloadType.IMAGE
        result.payload = input_frame.payloads[0]
        result_wrapper.results.append(result)

        return result_wrapper

    def run(self):
        self.engine_runner.run()

    async def run_async(self):
        logger.info(f"Running engine {self.engine_id} asynchronously")
        await self.engine_runner.run_async()

    async def stop(self):
        await self.engine_runner.stop()


@pytest.fixture(autouse=True)
def enable_asyncio_debug():
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.5  # seconds


@pytest.fixture(scope="session")
def server_frontend_port_generator():
    return itertools.count(90999)


@pytest.fixture
def server_frontend_port(server_frontend_port_generator):
    return next(server_frontend_port_generator)


@pytest.fixture(scope="session")
def server_backend_port_generator():
    return itertools.count(55555)


@pytest.fixture
def server_backend_port(server_backend_port_generator):
    return next(server_backend_port_generator)


@pytest.fixture
def use_zeromq():
    return True


@pytest.fixture
def use_ipc():
    return False


@pytest.fixture
def num_engines():
    return 1


@pytest.fixture(scope="session")
def prometheus_port_generator():
    return itertools.count(8001)


@pytest.fixture
def prometheus_port(prometheus_port_generator):
    return next(prometheus_port_generator)


@pytest_asyncio.fixture
async def run_server(
    server_frontend_port,
    server_backend_port,
    use_zeromq,
    prometheus_port,
    use_ipc,
):
    logger.info(
        f"Starting server: {use_zeromq=} {server_backend_port=} {server_frontend_port=} {use_ipc=}"
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
    return ["Engine-0"]


@pytest.fixture
def input_producer(target_engines):
    logger.info(f"Target engines: {target_engines}")

    async def producer():
        logger.info("Producing input")
        frame = gabriel_pb2.InputFrame()
        frame.payload_type = gabriel_pb2.PayloadType.TEXT
        frame.payloads.append(b"Hello from client")
        await asyncio.sleep(0.1)
        return frame

    producer = InputProducer(
        producer=producer, target_engine_ids=target_engines
    )
    yield [producer]
    producer.stop()


@pytest.fixture
def response_state():
    return {"received": False}


def get_consumer(response_state):
    def consumer(result_wrapper):
        logger.info(f"Status is {result_wrapper.status}")
        logger.info(f"Produced by {result_wrapper.result_producer_name.value}")
        response_state["received"] = True

    return consumer


@pytest.mark.asyncio
async def test_zeromq_client(
    run_engines, input_producer, server_frontend_port, response_state
):
    response_state.clear()
    response_state["received"] = False

    logger.info("Starting test_zeromq_client")

    client = ZeroMQClient(
        DEFAULT_SERVER_HOST,
        server_frontend_port,
        input_producer,
        get_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    for i in range(10):
        await asyncio.sleep(0.1)
        if response_state["received"]:
            break
    task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelled():
            raise
    logger.info("Client task is cancelled")

    assert response_state["received"]


def get_multiple_engine_consumer(response_state):
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
    """
    Test that we receive a response from each engine we target.
    """
    response_state.clear()

    logger.info(f"{server_frontend_port=}")

    client = ZeroMQClient(
        DEFAULT_SERVER_HOST,
        server_frontend_port,
        input_producer,
        get_multiple_engine_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    try:
        await asyncio.wait_for(task, timeout=1)
    except (TimeoutError, asyncio.TimeoutError):
        pass

    assert len(response_state) == len(target_engines)


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [["local_engine"]])
async def test_local_server(
    input_producer, server_frontend_port, response_state
):
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
        DEFAULT_SERVER_HOST,
        server_frontend_port,
        input_producer,
        get_consumer(response_state),
    )
    client_task = asyncio.create_task(client.launch_async())

    logger.info("Waiting for response from local engine")

    received = False
    for i in range(10):
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
        if asyncio.current_task().cancelled():
            raise
    try:
        logger.info("Waiting for engine task to cancel")
        await engine_task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelled():
            raise

    logger.info("Client and engine tasks are cancelled")

    assert response_state["received"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target_engines", [["local_engine"]])
async def test_ipc_local_engine(
    input_producer, server_frontend_port, response_state
):
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
        server_frontend_port,
        input_producer,
        get_consumer(response_state),
        use_ipc=True,
    )
    client_task = asyncio.create_task(client.launch_async())

    logger.info("Waiting for response from local engine")

    received = False
    for i in range(10):
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
        if asyncio.current_task().cancelled():
            raise
    try:
        logger.info("Waiting for engine task to cancel")
        await engine_task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelled():
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
    """
    Test that we receive a response from each engine we target, using ipc.
    """
    response_state.clear()

    client = ZeroMQClient(
        f"ipc:///tmp/gabriel_server_{server_frontend_port}.ipc",
        server_frontend_port,
        input_producer,
        get_multiple_engine_consumer(response_state),
        use_ipc=True,
    )
    task = asyncio.create_task(client.launch_async())

    try:
        await asyncio.wait_for(task, timeout=1)
    except (TimeoutError, asyncio.TimeoutError):
        pass
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
    response_state.clear()
    logger.info(f"{server_frontend_port=}")

    client = ZeroMQClient(
        DEFAULT_SERVER_HOST,
        server_frontend_port,
        input_producer,
        get_multiple_engine_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=1)
    except (TimeoutError, asyncio.TimeoutError):
        pass

    assert len(response_state) == 1

    input_producer[0].stop()
    input_producer[0].start(target_engine_ids=["Engine-0", "Engine-1"])

    try:
        await asyncio.wait_for(task, timeout=1)
    except (TimeoutError, asyncio.TimeoutError):
        pass

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
    response_state.clear()

    client = ZeroMQClient(
        DEFAULT_SERVER_HOST,
        server_frontend_port,
        input_producer,
        get_multiple_engine_consumer(response_state),
    )
    task = asyncio.create_task(client.launch_async())

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=1)
    except (TimeoutError, asyncio.TimeoutError):
        pass

    assert len(response_state) == 1

    # Simulate server disconnection
    logger.info(
        "Stopping server client handler to simulate disconnection******************"
    )
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
