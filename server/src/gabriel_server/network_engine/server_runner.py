import asyncio
from collections import deque
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


Metadata = namedtuple("Metadata", ["frame_id", "source_id", "client_address"])


MetadataPayload = namedtuple("MetadataPayload", ["metadata", "payload"])


def run(
    websocket_port,
    zmq_address,
    num_tokens,
    input_queue_maxsize,
    timeout=FIVE_SECONDS,
    message_max_size=None,
    use_zeromq=False,
):
    context = zmq.asyncio.Context()
    zmq_socket = context.socket(zmq.ROUTER)
    zmq_socket.bind(zmq_address)
    logger.info("Waiting for engines to connect")

    server = _Server(num_tokens, zmq_socket, timeout, input_queue_maxsize, use_zeromq)
    server.launch(websocket_port, message_max_size)


class _Server:
    def __init__(self, num_tokens, zmq_socket, timeout, size_for_queues, use_zeromq):
        self._zmq_socket = zmq_socket
        self._engine_workers = {}
        # Mapping from source id to source info
        self._source_infos: dict[str, _SourceInfo] = {}
        self._timeout = timeout
        self._size_for_queues = size_for_queues
        self._server = (ZeroMQServer if use_zeromq else WebsocketServer)(
            num_tokens, self._send_to_engine
        )

    def launch(self, client_port, message_max_size):
        asyncio.run(self.launch_async(client_port, message_max_size))

    async def launch_async(self, client_port, message_max_size):
        async def receive_from_engine_worker_loop():
            await self._server.wait_for_start()
            while self._server.is_running():
                await self._receive_from_engine_worker_helper()

        async def heartbeat_loop():
            await self._server.wait_for_start()
            while self._server.is_running():
                await asyncio.sleep(self._timeout)
                await self._heartbeat_helper()
        
        engine_receiver_task = asyncio.create_task(receive_from_engine_worker_loop())
        engine_heartbeat_task = asyncio.create_task(heartbeat_loop())

        engine_receiver_task.add_done_callback(lambda t: t.result())
        engine_heartbeat_task.add_done_callback(lambda t: t.result())
        
        server_task = self._server.launch_async(client_port, message_max_size)

        await asyncio.gather(engine_receiver_task, engine_heartbeat_task, server_task)

    async def _receive_from_engine_worker_helper(self):
        """Consume from ZeroMQ queue for cognitive engines messages"""
        address, _, payload = await self._zmq_socket.recv_multipart()

        engine_worker = self._engine_workers.get(address)
        if payload == network_engine.HEARTBEAT:
            if engine_worker is None:
                logger.error("Heartbeat from unknown engine")
            else:
                engine_worker.record_heatbeat()
            return

        from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
        from_standalone_engine.ParseFromString(payload)
        if engine_worker is None:
            await self._add_engine_worker(address, from_standalone_engine)
            return

        if from_standalone_engine.HasField("welcome"):
            logger.error("Engine sent duplicate welcome message")
            return

        result_wrapper = from_standalone_engine.result_wrapper
        engine_worker_metadata = engine_worker.get_current_input_metadata()
        source_info = self._source_infos.get(engine_worker_metadata.source_id)
        if source_info is None:
            logger.error("Source info not found")
            return
        latest_input = source_info.get_latest_input()

        # Check if the result corresponds to the latest input for this source. If so,
        # always send the result back
        if latest_input is not None and latest_input.metadata == engine_worker_metadata:
            # Send response to client
            await self._server.send_result_wrapper(
                engine_worker_metadata.client_address,
                source_info.get_name(),
                engine_worker_metadata.frame_id,
                engine_worker.get_engine_name(),
                result_wrapper,
                return_token=True,
            )

            # Send the next input to the engine from the queue
            await engine_worker.send_message_from_queue()
            return

        if engine_worker.get_all_responses_required():
            await self._server.send_result_wrapper(
                engine_worker_metadata.client_address,
                source_info.get_name(),
                engine_worker_metadata.frame_id,
                engine_worker.get_engine_name(),
                result_wrapper,
                return_token=False,
            )

        if latest_input is None:
            # There is no new input to give the worker
            engine_worker.clear_current_input_metadata()
        else:
            # Give the worker the latest input
            await engine_worker.send_payload(latest_input)

    async def _add_engine_worker(self, address, from_standalone_engine):
        if not from_standalone_engine.HasField("welcome"):
            logger.warning(
                "Non-welcome message from unknown engine. Consider increasing timeout."
            )
            return

        welcome = from_standalone_engine.welcome
        engine_name = welcome.engine_name
        logger.info(f"New engine {engine_name} connected")

        all_responses_required = welcome.all_responses_required
        engine_worker = _EngineWorker(
            self._zmq_socket,
            address,
            engine_name,
            all_responses_required,
            self._size_for_queues,
        )
        self._engine_workers[address] = engine_worker

    async def _heartbeat_helper(self):
        current_time = time.time()
        # We cannot directly iterate over items because we delete some entries
        for address, engine_worker in list(self._engine_workers.items()):
            if (current_time - engine_worker.get_last_sent()) < self._timeout:
                continue

            if (
                not engine_worker.get_awaiting_heartbeat_response()
            ) and engine_worker.get_current_input_metadata() is None:
                await engine_worker.send_heartbeat()
                continue

            logger.info(
                f"Lost connection to engine worker {engine_worker.get_engine_name()}"
            )

            current_input_metadata = engine_worker.get_current_input_metadata()

            if current_input_metadata is None:
                logger.info("Engine disconnected while it was idle")
                del self._engine_workers[address]
                return

            source_info = self._source_infos.get(
                engine_worker.current_input_metadata.source_id
            )
            if source_info is None:
                logger.error("Source info not found")
                return

            latest_input = source_info.get_latest_input()
            current_input_metadata = engine_worker.get_current_input_metadata()
            if (
                latest_input is not None
                and current_input_metadata == latest_input.metadata
            ):
                # Return token for frame engine was in the middle of processing
                status = gabriel_pb2.ResultWrapper.Status.ENGINE_ERROR
                result_wrapper = cognitive_engine.create_result_wrapper(status)
                await self._server.send_result_wrapper(
                    current_input_metadata.client_address,
                    source_info.get_name(),
                    current_input_metadata.frame_id,
                    engine_worker.get_engine_name(),
                    result_wrapper,
                    return_token=True,
                )

            del self._engine_workers[address]

    async def _send_to_engine(self, from_client, client_address):
        if from_client.source_id not in self._source_infos:
            self._source_infos[from_client.source_id] = _SourceInfo(
                from_client.source_id, self._engine_workers
            )
        source_info = self._source_infos[from_client.source_id]
        return await source_info.process_input_from_client(from_client, client_address)


class _EngineWorker:
    def __init__(
        self,
        zmq_socket,
        address,
        engine_name,
        all_responses_required,
        fresh_inputs_queue_size,
    ):
        self._zmq_socket = zmq_socket
        self._address = address
        self._engine_name = engine_name
        self._all_responses_required = all_responses_required
        self._last_sent = 0
        self._awaiting_heartbeat_response = False
        self._current_input_metadata = None
        # Mapping of source ids to their input queues
        self._source_queues = dict()
        # Maximum size for each source queue
        self._size_for_queues = fresh_inputs_queue_size
        # Iterator for iterating through source queues in a round-robin manner
        self._source_queue_iterator = FairQueueIterator(self._source_queues)

    def get_address(self):
        return self._address

    def get_engine_name(self):
        return self._engine_name

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
        """Send the payload to the cognitive engine"""
        await self._zmq_socket.send_multipart([self._address, b"", payload])
        self._last_sent = time.time()

    async def send_payload(self, metadata_payload):
        if self._current_input_metadata is not None:
            await self.add_input_to_queue(metadata_payload)
            return
        self._current_input_metadata = metadata_payload.metadata
        await self._send_helper(metadata_payload.payload)

    async def send_message_from_queue(self):
        """Send message from queue and update current input.

        Current input will be set as None if there is nothing on the queue."""

        source_queue = next(self._source_queue_iterator, None)
        if source_queue is None:
            self._current_input_metadata = None
            return

        metadata_payload = await source_queue.get()
        await self.send_payload(metadata_payload)

    async def create_new_queue(self, source_id):
        queue = asyncio.Queue(maxsize=self._size_for_queues)
        self._source_queues[source_id] = queue
        self._source_queue_iterator.add_new_queue(queue)

    async def add_input_to_queue(self, metadata_payload):
        source_id = metadata_payload.metadata.source_id
        if source_id not in self._source_queues:
            await self.create_new_queue(source_id)
        self._source_queues[source_id].put_nowait(metadata_payload)


class _SourceInfo:
    def __init__(self, source_id, engine_workers):
        self._source_id = source_id
        self._latest_input = None
        self._engine_workers = engine_workers

    def get_name(self):
        return self._source_id

    def get_latest_input(self):
        return self._latest_input

    async def process_input_from_client(self, from_client, client_address):
        """
        Process input received from a client. Send it to the targetted engine workers.
        """
        logger.debug(
            f"Processing input from client {client_address} with source ID {from_client.source_id} and frame id {from_client.frame_id}"
        )
        metadata = Metadata(
            frame_id=from_client.frame_id,
            source_id=self._source_id,
            client_address=client_address,
        )
        payload = from_client.input_frame.SerializeToString()
        metadata_payload = MetadataPayload(metadata=metadata, payload=payload)

        target_engines = []
        for engine_worker in self._engine_workers.values():
            if engine_worker.get_engine_name() in from_client.target_engine_ids:
                target_engines.append(engine_worker)

        if target_engines == []:
            # TODO: better error handling
            logger.error("No target engines found")
            return False

        for engine_worker in target_engines:
            await engine_worker.send_payload(metadata_payload)

        self._latest_input = metadata_payload
        return True


class FairQueueIterator:
    """
    Iterate over multiple queues in a fair manner.
    """

    def __init__(self, queues):
        self.queues = deque(queues)

    def add_new_queue(self, queue):
        self.queues.append(queue)

    def __iter__(self):
        return self

    def __next__(self):
        # Iterate through all the queues, stopping when we find a non-empty one
        for _ in range(len(self.queues)):
            queue = self.queues.popleft()
            self.queues.append(queue)
            if queue:
                # If queue is not empty, return it
                return queue
        raise StopIteration
