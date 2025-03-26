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
from gabriel_server.zeromq_server import ZeroMQServer


FIVE_SECONDS = 5


logger = logging.getLogger(__name__)


Metadata = namedtuple('Metadata', ['frame_id', 'client_address', 'token_bucket'])


MetadataPayload = namedtuple('MetadataPayload', ['metadata', 'payload'])


async def run(websocket_port, zmq_address, num_tokens, input_queue_maxsize,
              timeout=FIVE_SECONDS, message_max_size=None, use_zeromq=False):
    context = zmq.asyncio.Context()
    zmq_socket = context.socket(zmq.ROUTER)
    zmq_socket.bind(zmq_address)
    logger.info('Waiting for engines to connect')

    server = _Server(num_tokens, zmq_socket, timeout, input_queue_maxsize, use_zeromq)
    await server.launch(websocket_port, message_max_size)

class _Server:
    def __init__(self, num_tokens, zmq_socket, timeout, size_for_queues, use_zeromq):
        self._zmq_socket = zmq_socket
        self._engine_workers = {}
        self._engine_groups = {}
        self._timeout = timeout
        self._size_for_queues = size_for_queues
        self._server = (
            ZeroMQServer if use_zeromq else WebsocketServer)(num_tokens, self._send_to_engine_group)

    async def cleanup(self):
        '''Cleanup background asyncio tasks'''
        if not self._engine_worker_task.done():
            self._engine_worker_task.cancel()
            try:
                await self._engine_worker_task
            except asyncio.CancelledError:
                pass

        if not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

    async def launch(self, client_port, message_max_size):
        async def receive_from_engine_worker_loop():
            await self._server.wait_for_start()
            while self._server.is_running():
                await self._receive_from_engine_worker_helper()

        async def heartbeat_loop():
            await self._server.wait_for_start()
            while self._server.is_running():
                await asyncio.sleep(self._timeout)
                await self._heartbeat_helper()

        self._engine_worker_task = asyncio.create_task(receive_from_engine_worker_loop())
        self._heartbeat_task = asyncio.create_task(heartbeat_loop())
        self._server_task = asyncio.create_task(self._server.launch(client_port, message_max_size))

        try:
            await asyncio.gather(self._server_task, self._engine_worker_task,
                                 self._heartbeat_task)
        except asyncio.CancelledError:
            await self.cleanup()
            raise

    async def _receive_from_engine_worker_helper(self):
        '''Consume from ZeroMQ queue for cognitive engines messages'''
        address, _, payload = await self._zmq_socket.recv_multipart()

        engine_worker = self._engine_workers.get(address)
        if payload == network_engine.HEARTBEAT:
            if engine_worker is None:
                logger.error('Heartbeat from unknown engine')
            else:
                engine_worker.record_heatbeat()
            return

        from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
        from_standalone_engine.ParseFromString(payload)
        if engine_worker is None:
            await self._add_engine_worker(address, from_standalone_engine)
            return

        if from_standalone_engine.HasField('welcome'):
            logger.error('Engine sent duplicate welcome message')
            return

        result_wrapper = from_standalone_engine.result_wrapper
        engine_worker_metadata = engine_worker.get_current_input_metadata()
        engine_group = engine_worker.get_engine_group()
        latest_input = engine_group.get_latest_input()

        logger.info(f"Got result from engine {engine_worker.get_name()}")
        if (latest_input is not None and
            latest_input.metadata == engine_worker_metadata):
            # Send response to client
            await self._server.send_result_wrapper(
                engine_worker.get_name(),
                engine_worker_metadata.client_address,
                engine_group.computation_type(),
                engine_worker_metadata.token_bucket,
                engine_worker_metadata.frame_id, result_wrapper,
                return_token=True)
            await engine_worker.send_message_from_queue()
            return

        if engine_worker.get_all_responses_required():
            await self._server.send_result_wrapper(
                engine_worker.get_name(),
                engine_worker_metadata.client_address,
                engine_group.computation_type(),
                engine_worker_metadata.token_bucket,
                engine_worker_metadata.frame_id, result_wrapper,
                return_token=False)

        if latest_input is None:
            # There is no new input to give the worker
            engine_worker.clear_current_input_metadata()
        else:
            # Give the worker the latest input
            await engine_worker.send_payload(latest_input)

    async def _add_engine_worker(self, address, from_standalone_engine):
        if not from_standalone_engine.HasField('welcome'):
            logger.warning('Non-welcome message from unknown engine. Consider '
                           'increasing timeout.')
            return

        welcome = from_standalone_engine.welcome
        engine_name = welcome.engine_name
        computation_type = welcome.computation_type
        logger.info(f'Engine {engine_name} connected that performs {computation_type}')

        engine_group = self._engine_groups.get(computation_type)
        if engine_group is None:
            logger.info(f'First engine connected that performs {computation_type}: {engine_name}')
            engine_group = _EngineGroup(computation_type, self._size_for_queues)
            self._engine_groups[computation_type] = engine_group

            # Tell server to accept inputs for this computation type
            self._server.add_computation_type(computation_type)

        all_responses_required = welcome.all_responses_required
        engine_worker = _EngineWorker(
            self._zmq_socket, engine_group, address, engine_name, all_responses_required)
        self._engine_workers[address] = engine_worker

        engine_group.add_engine_worker(engine_worker)

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

            engine_group = engine_worker.get_engine_group()
            logger.info(f'Lost connection to engine worker that performs {engine_group.computation_type()}')

            latest_input = engine_group.get_latest_input()
            current_input_metadata = engine_worker.get_current_input_metadata()
            if (latest_input is not None and
                current_input_metadata == latest_input.metadata):
                # Return token for frame engine was in the middle of processing
                status = gabriel_pb2.ResultWrapper.Status.ENGINE_ERROR
                result_wrapper = cognitive_engine.create_result_wrapper(status)
                await self._server.send_result_wrapper(
                    engine_worker.get_name(),
                    current_input_metadata.client_address,
                    engine_group.computation_type(),
                    current_input_metadata.token_bucket,
                    current_input_metadata.frame_id, result_wrapper,
                    return_token=True)

            engine_group.remove_engine_worker(engine_worker)
            del self._engine_workers[address]

            if engine_group.has_no_engine_workers():
                computation_type = engine_group.computation_type()
                logger.info(f'No remaining engines perform {computation_type}')
                del self._engine_groups[computation_type]
                self._server.remove_computation_type(computation_type)

    async def _send_to_engine_group(self, from_client, client_address, computation_type):
        engine_group = self._engine_groups[computation_type]
        return await engine_group.process_input_from_client(
            from_client, client_address)

class _EngineWorker:
    """
    Represents a single Gabriel cognitive engine.
    """
    def __init__(
            self, zmq_socket, engine_group, address, name, all_responses_required):
        """
        Args:
            zmq_socket: the ZeroMQ socket to communicate with the engine
            engine_group: the engine group that this engine corresponds to
            address: the address of this engine
            name: the name of this engine
            all_responses_required:
                send the client results from this engine even if it is not
                the first engine to finish computing results on a new input

        """
        self._zmq_socket = zmq_socket
        self._engine_group = engine_group
        self._address = address
        self._all_responses_required = all_responses_required
        self._last_sent = 0
        self._awaiting_heartbeat_response = False
        self._current_input_metadata = None
        self._name = name

    def get_name(self):
        return self._name

    def get_address(self):
        return self._address

    def get_engine_group(self):
        return self._engine_group

    def get_current_input_metadata(self):
        return self._current_input_metadata

    def get_all_responses_required(self):
        return self._all_responses_required

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
        '''Send the payload to the cognitive engine'''
        await self._zmq_socket.send_multipart([self._address, b'', payload])
        self._last_sent = time.time()

    async def send_payload(self, metadata_payload):
        self._current_input_metadata = metadata_payload.metadata
        await self._send_helper(metadata_payload.payload)

    async def send_message_from_queue(self):
        '''Send message from queue and update current input.

        Current input will be set as None if there is nothing on the queue.'''
        metadata_payload = self._engine_group.advance_unsent_queue()

        if metadata_payload is None:
            self._current_input_metadata = None
        else:
            await self.send_payload(metadata_payload)


class _EngineGroup:
    """
    Manages a group of engines that perform the same type of computation.
    """
    def __init__(self, computation_type, fresh_inputs_queue_size):
        """
        Args:
            computation_type (str):
                The type of computation performed by this engine group
            fresh_inputs_queue_size (int):
                The size of the unsent inputs queue
        """
        self._computation_type = computation_type
        self._unsent_inputs = asyncio.Queue(maxsize=fresh_inputs_queue_size)
        self._latest_input = None
        self._engine_workers = set()

    def computation_type(self):
        return self._computation_type

    def add_engine_worker(self, engine_worker):
        self._engine_workers.add(engine_worker)

    def remove_engine_worker(self, engine_worker):
        self._engine_workers.remove(engine_worker)

    def has_no_engine_workers(self):
        return len(self._engine_workers) == 0

    def get_latest_input(self):
        return self._latest_input

    async def process_input_from_client(self, from_client, client_address):
        sent_to_engine = False
        metadata = Metadata(
            frame_id=from_client.frame_id, client_address=client_address,
            token_bucket=from_client.token_bucket)
        payload = from_client.input_frame.SerializeToString()
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

    def advance_unsent_queue(self):
        '''
        Remove an item from the queue of unsent input_frame messages, and store
        this as the latest input.

        Return the latest item, or None if the queue was empty
        '''

        self._latest_input = (None if self._unsent_inputs.empty() else
                              self._unsent_inputs.get_nowait())

        return self._latest_input
