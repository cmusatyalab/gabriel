import asyncio
import time
import logging
import zmq
import zmq.asyncio
from collections import namedtuple
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server import network_engine
from gabriel_server.websocket_server import WebsocketServer


FIVE_SECONDS = 5


logger = logging.getLogger(__name__)


Metadata = namedtuple('Metadata', ['frame_id', 'host', 'port'])


MetadataPayload = namedtuple('MetadataPayload', ['metadata', 'payload'])


def run(websocket_port, zmq_address, num_tokens, input_queue_maxsize,
        timeout=FIVE_SECONDS):
    context = zmq.asyncio.Context()
    zmq_socket = context.socket(zmq.ROUTER)
    zmq_socket.bind(zmq_address)
    logger.info('Waiting for engines to connect')

    server = _Server(websocket_port, num_tokens, zmq_socket, timeout,
                     input_queue_maxsize)
    server.launch()


class _Server(WebsocketServer):
    def __init__(self, websocket_port, num_tokens, zmq_socket, timeout,
                 size_for_queues):
        super().__init__(websocket_port, num_tokens)

        self._zmq_socket = zmq_socket
        self._engine_workers = {}
        self._source_infos = {}
        self._from_engines = asyncio.Queue()
        self._timeout = timeout
        self._size_for_queues = size_for_queues

    def launch(self):
        # We cannot use while self.is_running because these loops would
        # terminate before super().launch() is called launch
        async def receive_from_engine_worker_loop():
            while True:
                await self._receive_from_engine_worker_helper()

        async def heartbeat_loop():
            while True:
                await asyncio.sleep(self._timeout)
                await self._heartbeat_helper()

        asyncio.ensure_future(receive_from_engine_worker_loop())
        asyncio.ensure_future(heartbeat_loop())
        super().launch()

    async def _receive_from_engine_worker_helper(self):
        address, _, payload = await self._zmq_socket.recv_multipart()

        engine_worker = self._engine_workers.get(address)
        if payload == network_engine.HEARTBEAT:
            if engine_worker == None:
                logger.error('Heartbeat from unknown engine')
            else:
                engine_worker.record_heatbeat()
            return
        to_server_runner = gabriel_pb2.ToServerRunner()
        to_server_runner.ParseFromString(payload)

        if engine_worker is None:
            await self._add_engine_worker(address, to_server_runner)
            return

        if to_server_runner.HasField('welcome'):
            logger.error('Engine sent duplicate welcome message')
            return

        result_wrapper = to_server_runner.result_wrapper
        engine_worker_metadata = engine_worker.get_current_input_metadata()
        source_info = engine_worker.get_source_info()
        latest_input = source_info.get_latest_input()
        if (latest_input is not None and
            latest_input.metadata == engine_worker_metadata):
            # Send response to client
            await source_info.respond_to_client(
                engine_worker_metadata, result_wrapper, return_token=True)
            await engine_worker.send_message_from_queue()
            return

        if len(result_wrapper.results) > 0:
            await source_info.respond_to_client(
                engine_worker_metadata, result_wrapper, return_token=False)

        if latest_input is None:
            # There is no new input to give the worker
            engine_worker.clear_current_input_metadata()
        else:
            # Give the worker the latest input
            await engine_worker.send_payload(latest_input)

    async def _add_engine_worker(self, address, to_server_runner):
        if to_server_runner.HasField('welcome'):
            source_name = to_server_runner.welcome.source_name
            logger.info('New engine connected that consumes frames from '
                        'source: %s', source_name)
        elif to_server_runner.HasField('result_wrapper'):
            # The engine was probably running for too long.
            source_name = to_server_runner.result_wrapper.source_name
            logger.warning('Result from unrecognized engines that consumes '
                           'frames from source: %s', source_name)
            logger.info('Adding engine as if it were new')

        source_info = self._source_infos.get(source_name)
        if source_info is None:
            logger.info('First engine for inputs from source: %s', source_name)
            source_info = _SourceInfo(
                source_name, self._size_for_queues, self._from_engines)
            self._source_infos[source_name] = source_info

            # Tell super() to accept inputs from source_name
            self.add_source_consumed(source_name)

        engine_worker = _EngineWorker(self._zmq_socket, source_info, address)
        self._engine_workers[address] = engine_worker

        source_info.add_engine_worker(engine_worker)

    async def _heartbeat_helper(self):
        current_time = time.time()
        # We cannot directly iterate over items because we delete some entries
        for address, engine_worker in list(self._engine_workers.items()):
            if (current_time - engine_worker.get_last_sent()) < self._timeout:
                continue

            if ((not engine_worker.get_awaiting_heartbeat_response()) and
                engine_worker.get_current_input_metadata() is None):
                await engine_worker.send_heartbeat()
                continue

            source_info = engine_worker.get_source_info()
            logger.info('Lost connection to engine worker that consumes items '
                        'from source: %s', source_info.get_name())
            await engine_worker.drop()
            del self._engine_workers[address]

            if source_info.has_no_engine_workers():
                source_name = source_info.get_name()
                logger.info('No remaining engines consume input from source: '
                            '%s', source_name)
                del self._source_infos[source_name]
                self.remove_source_consumed(source_name)

    async def _send_to_engine(self, to_engine):
        source_name = to_engine.from_client.source_passed
        source_info = self._source_infos[source_name]

        return await source_info.handle_new_to_engine(to_engine)

    async def _recv_from_engine(self):
        return await self._from_engines.get()


class _EngineWorker:
    def __init__(self, zmq_socket, source_info, address):
        self._zmq_socket = zmq_socket
        self._source_info = source_info
        self._address = address
        self._last_sent = 0
        self._awaiting_heartbeat_response = False
        self._current_input_metadata = None

    def get_address(self):
        return self._address

    def get_source_info(self):
        return self._source_info

    def get_current_input_metadata(self):
        return self._current_input_metadata

    def clear_current_input_metadata(self):
        self._current_input_metadata = None

    def record_heatbeat(self):
        self._awaiting_heartbeat_response = False

    def get_awaiting_heartbeat_response(self):
        return self._awaiting_heartbeat_response

    def get_last_sent(self):
        return self._last_sent

    async def send_heartbeat(self):
        await self._send_helper(network_engine.HEARTBEAT)
        self._awaiting_heartbeat_response = True

    async def _send_helper(self, payload):
        await self._zmq_socket.send_multipart([self._address, b'', payload])
        self._last_sent = time.time()

    async def drop(self):
        latest_input = self._source_info.get_latest_input()
        if (latest_input is not None and
            self._current_input_metadata == latest_input.metadata):
            # Return token for frame engine was in the middle of processing
            status = gabriel_pb2.ResultWrapper.Status.ENGINE_ERROR
            metadata = self._current_input_metadata
            source_name = self._source_info.get_name()
            result_wrapper = cognitive_engine.error_result_wrapper(
                metadata.frame_id, status, source_name)
            await self._source_info.respond_to_client(
                metadata, result_wrapper, return_token=True)

        self._source_info.remove_engine_worker(self)

    async def send_payload(self, metadata_payload):
        self._current_input_metadata = metadata_payload.metadata
        await self._send_helper(metadata_payload.payload)

    async def send_message_from_queue(self):
        '''Send message from queue and update current input.

        Current input will be set as None if there is nothing on the queue.'''
        metadata_payload = self._source_info.advance_unsent_queue()

        if metadata_payload is None:
            self._current_input_metadata = None
        else:
            await self.send_payload(metadata_payload)

class _SourceInfo:
    def __init__(self, source_name, fresh_inputs_queue_size, from_engines):
        self._source_name = source_name
        self._unsent_inputs = asyncio.Queue(maxsize=fresh_inputs_queue_size)
        self._from_engines = from_engines
        self._latest_input = None
        self._engine_workers = set()

    def get_name(self):
        return self._source_name

    def add_engine_worker(self, engine_worker):
        self._engine_workers.add(engine_worker)

    def remove_engine_worker(self, engine_worker):
        self._engine_workers.remove(engine_worker)

    def has_no_engine_workers(self):
        return len(self._engine_workers) == 0

    def get_latest_input(self):
        return self._latest_input

    async def handle_new_to_engine(self, to_engine):
        sent_to_engine = False
        metadata = Metadata(frame_id=to_engine.from_client.frame_id,
                            host=to_engine.host, port=to_engine.port)
        payload = to_engine.from_client.SerializeToString()
        metadata_payload = MetadataPayload(metadata=metadata, payload=payload)
        for engine_worker in self._engine_workers:
            if engine_worker.get_current_input_metadata() is None:
                await engine_worker.send_payload(metadata_payload)
                sent_to_engine = True

        if sent_to_engine:
            self._latest_input = metadata_payload
            return True

        if self._unsent_inputs.full():
            return False

        self._unsent_inputs.put_nowait(metadata_payload)
        return True

    async def respond_to_client(self, metadata, result_wrapper, return_token):
        result_wrapper.source_name = self.get_name()
        result_wrapper.frame_id = metadata.frame_id
        result_wrapper.return_token = return_token
        from_engine = cognitive_engine.pack_from_engine(
            metadata.host, metadata.port, result_wrapper, return_token)
        await self._from_engines.put(from_engine)

        if return_token:
            self._latest_input = None

    def advance_unsent_queue(self):
        '''
        Remove an item from the queue of unsent to_engine messages, and store
        this as the latest input.

        Return metadata_payload if there was an item pulled off the queue.
        Return None otherwise.
        '''

        if self._unsent_inputs.empty():
            # We do not need to update latest input, because respond_to_client
            # has already cleared latest_input
            return None

        metadata_payload = self._unsent_inputs.get_nowait()
        self._latest_input = metadata_payload

        return metadata_payload
