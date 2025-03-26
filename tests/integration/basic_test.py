import asyncio
import concurrent.futures
import itertools
import logging
import pytest
import pytest_asyncio
import threading
import time

from gabriel_client.gabriel_client import ProducerWrapper
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2
from gabriel_server.network_engine import server_runner
from gabriel_server.network_engine import engine_runner
from gabriel_server import cognitive_engine

DEFAULT_NUM_TOKENS = 2
DEFAULT_SERVER_HOST = 'localhost'
INPUT_QUEUE_MAXSIZE = 60

logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.DEBUG)
logger = logging.getLogger(__name__)

#@pytest.fixture(scope="session")
#def event_loop():
#    try:
#        loop = asyncio.get_running_loop()
#    except RuntimeError:
#        loop = asyncio.new_event_loop()
#    yield loop
#    loop.close()

class Engine(cognitive_engine.Engine, threading.Thread):

    def __init__(self, engine_id, computation_type, zeromq_address):
        super().__init__()
        self.engine_id = engine_id
        self.computation_type = computation_type
        self.zeromq_address = zeromq_address
        self.engine_runner = engine_runner.EngineRunner(
            self, self.computation_type, self.zeromq_address,
            f"Engine-{self.engine_id}", all_responses_required=True, timeout=1000, request_retries=3)

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
        await self.engine_runner.run_async()

    def stop(self):
        self.engine_runner.stop()

@pytest.fixture(scope="session")
def client_port_generator():
    return itertools.count(9099)

@pytest.fixture
def client_port(client_port_generator):
    return next(client_port_generator)

@pytest.fixture(scope="session")
def engine_port_generator():
    return itertools.count(5555)

@pytest.fixture
def engine_port(engine_port_generator):
    return next(engine_port_generator)

@pytest.fixture
def use_zeromq():
    return True

@pytest.fixture
def num_engines():
    return 1

@pytest_asyncio.fixture(loop_scope="session")
async def run_server(client_port, engine_port, use_zeromq):
    logger.info(f"Starting server: {use_zeromq=} {engine_port=} {client_port=}")
    task = asyncio.create_task(server_runner.run(
        websocket_port=client_port, zmq_address=f"tcp://*:{engine_port}",
        num_tokens=DEFAULT_NUM_TOKENS, input_queue_maxsize=INPUT_QUEUE_MAXSIZE,
        use_zeromq=use_zeromq))
    yield task
    logger.info("Tearing down server")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    logger.info("Done tearing down server")

@pytest_asyncio.fixture(loop_scope="session")
async def run_engines(run_server, engine_port, num_engines):
    engines = []
    logger.info(f"Running engines, connecting to {engine_port=}!")

    for engine_id in range(num_engines):
        computation_type = f"computation_type-{engine_id}"
        zeromq_address = f'tcp://localhost:{engine_port}'
        engine = Engine(engine_id, computation_type, zeromq_address)
        engines.append(asyncio.create_task(engine.run_async()))

    yield engines
    logger.info("Tearing down engines")
    for task in engines:
        task.cancel()
    await asyncio.gather(*engines, return_exceptions=True)
    logger.info("Done tearing down engines")

@pytest.fixture
def target_computation_types():
    return ["computation_type-0"]

@pytest.fixture
def producer_wrappers(target_computation_types):
    async def producer():
        logger.info("Producing input")
        frame = gabriel_pb2.InputFrame()
        frame.payload_type = gabriel_pb2.PayloadType.TEXT
        frame.payloads.append(b'Hello from client')
        await asyncio.sleep(0.1)
        return frame

    return [
        ProducerWrapper(producer=producer, token_bucket='default-token-bucket', target_computation_types=target_computation_types)
    ]

response_received = False

def consumer(result_wrapper):
    logger.info(f"Status is {result_wrapper.status}")
    logger.info(f"Produced by {result_wrapper.result_producer_name.value}")
    global response_received
    response_received = True

@pytest.mark.asyncio(loop_scope="session")
async def test_zeromq_client(run_engines, producer_wrappers, client_port):
    global response_received
    response_received = False

    client = ZeroMQClient(DEFAULT_SERVER_HOST, client_port, producer_wrappers, consumer)
    task = asyncio.create_task(client.launch_async())
    logger.info("Hello from test!")

    for i in range(10):
        await asyncio.sleep(0.1)
        if response_received:
            break
    task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelled():
            raise
    logger.info("Client task is cancelled")

    assert response_received

@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize('use_zeromq', [False])
async def test_websocket_client(producer_wrappers, client_port, run_engines):
    logger.info(f"{client_port=}")
    global response_received
    response_received = False

    client = WebsocketClient(DEFAULT_SERVER_HOST, client_port, producer_wrappers, consumer)
    task = asyncio.create_task(client.launch_async())
    logger.info("Hello from test!")


    while True:
        logger.info("Waiting for response from server")
        await asyncio.sleep(0.1)
        if response_received:
            logger.info("Received response!")
            break

    task.cancel()
    try:
        logger.info("Waiting for client task to cancel")
        await task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelled():
            raise
    logger.info("Client task is cancelled")

    assert response_received

responses = dict()

def multiple_engine_consumer(result_wrapper):
    logger.info(f"Status is {result_wrapper.status}")
    logger.info(f"Produced by {result_wrapper.result_producer_name.value}")
    global responses
    key = result_wrapper.result_producer_name.value
    responses[key] = responses.get(key, 0) + 1

@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize('target_computation_types', [["computation_type-0"], ["computation_type-0", "computation_type-1"], ["computation_type-0", "computation_type-1", "computation_type-2"]])
@pytest.mark.parametrize('num_engines', [3])
async def test_send_multiple_engines(producer_wrappers, client_port, target_computation_types, run_engines):
    global responses
    responses = dict()

    logger.info(f"{client_port=}")

    client = ZeroMQClient(DEFAULT_SERVER_HOST, client_port, producer_wrappers, multiple_engine_consumer)
    task = asyncio.create_task(client.launch_async())

    try:
        await asyncio.wait_for(task, timeout=1)
    except (TimeoutError, asyncio.TimeoutError):
        pass

    assert len(responses) == len(target_computation_types)

