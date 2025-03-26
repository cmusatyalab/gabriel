import logging
import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2
from gabriel_server import network_engine
import threading

TEN_SECONDS = 10000
REQUEST_RETRIES = 3


logger = logging.getLogger(__name__)

class EngineRunner:

    def __init__(self, engine, computation_type, server_address, engine_name,
                 all_responses_required, timeout, request_retries):

        self._context = zmq.Context()
        self._stop_event = threading.Event()
        self._engine = engine
        self._computation_type = computation_type
        self._server_address = server_address
        self._engine_name = engine_name
        self._all_responses_required = all_responses_required
        self._timeout = timeout
        self._request_retries = request_retries

    async def run_async(self):
        request_retries = self._request_retries

        context = zmq.asyncio.Context()

        while request_retries > 0 and not self._stop_event.is_set():
            socket = context.socket(zmq.REQ)
            socket.connect(self._server_address)
            from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
            from_standalone_engine.welcome.computation_type = self._computation_type
            from_standalone_engine.welcome.all_responses_required = (
                self._all_responses_required)
            from_standalone_engine.welcome.engine_name = self._engine_name
            await socket.send(from_standalone_engine.SerializeToString())
            logger.info('Sent welcome message to server')

            while not self._stop_event.is_set():
                if await socket.poll(self._timeout) == 0:
                    logger.warning('No response from server')
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.close()
                    request_retries -= 1
                    break

                message_from_server = await socket.recv()
                if message_from_server == network_engine.HEARTBEAT:
                    await socket.send(network_engine.HEARTBEAT)
                    continue

                input_frame = gabriel_pb2.InputFrame()
                input_frame.ParseFromString(message_from_server)
                result_wrapper = self._engine.handle(input_frame)

                from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
                from_standalone_engine.result_wrapper.CopyFrom(result_wrapper)
                await socket.send(from_standalone_engine.SerializeToString())

        if not self._stop_event.is_set():
            logger.warning('Ran out of retries. Abandoning server connection.')

    def run(self):
        request_retries = self._request_retries

        while request_retries > 0 and not self._stop_event.is_set():
            socket = self._context.socket(zmq.REQ)
            socket.connect(self._server_address)
            from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
            from_standalone_engine.welcome.computation_type = self._computation_type
            from_standalone_engine.welcome.all_responses_required = (
                self._all_responses_required)
            from_standalone_engine.welcome.engine_name = self._engine_name
            socket.send(from_standalone_engine.SerializeToString())
            logger.info('Sent welcome message to server')

            while not self._stop_event.is_set():
                if socket.poll(self._timeout) == 0:
                    logger.warning('No response from server')
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.close()
                    request_retries -= 1
                    break

                message_from_server = socket.recv()
                if message_from_server == network_engine.HEARTBEAT:
                    socket.send(network_engine.HEARTBEAT)
                    continue

                input_frame = gabriel_pb2.InputFrame()
                input_frame.ParseFromString(message_from_server)
                result_wrapper = self._engine.handle(input_frame)

                from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
                from_standalone_engine.result_wrapper.CopyFrom(result_wrapper)
                socket.send(from_standalone_engine.SerializeToString())

        if not self._stop_event.is_set():
            logger.warning('Ran out of retries. Abandoning server connection.')

    def stop(self):
        self._stop_event.set()

def run(engine, computation_type, server_address, engine_name,
        all_responses_required=False, timeout=TEN_SECONDS,
        request_retries=REQUEST_RETRIES):
    engine_runner = EngineRunner(
        engine, computation_type, server_address, engine_name,
        all_responses_required, timeout, request_retries)
    engine_runner.run()

