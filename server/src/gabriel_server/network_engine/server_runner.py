"""Run the Gabriel server that connects clients to cognitive engines."""

import asyncio
import logging
import time
from collections import deque, namedtuple
from typing import Optional, Union

import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2
from prometheus_client import Gauge, Histogram, start_http_server

from gabriel_server import cognitive_engine, network_engine
from gabriel_server.websocket_server import WebsocketServer
from gabriel_server.zeromq_server import ZeroMQServer

FIVE_SECONDS = 5

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) - "
    "%(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


Metadata = namedtuple(
    "Metadata",
    ["frame_id", "source_id", "client_address", "target_engine_ids"],
)


MetadataPayload = namedtuple("MetadataPayload", ["metadata", "payload"])

ENGINE_LATENCY = Histogram(
    "engine_processing_latency_seconds",
    "End-to-end engine processing latency",
    ["engine"],
)

SOURCE_QUEUE_LENGTH = Gauge(
    "source_queue_length",
    "Length of each source queue",
    ["source_id"],
)

INPUT_INTER_ARRIVAL_TIME = Histogram(
    "input_inter_arrival_time_seconds",
    "Time between arrivals of inputs from the same source",
    ["source_id"],
)


class ServerRunner:
    """Runs the Gabriel server that connects clients to engines."""

    def __init__(
        self,
        client_endpoint: Union[int, str],
        engine_zmq_endpoint: str,
        num_tokens: int,
        input_queue_maxsize: int,
        timeout: int = FIVE_SECONDS,
        message_max_size: Optional[int] = None,
        use_zeromq: bool = True,
        prometheus_port: int = 8000,
        use_ipc: bool = False,
    ):
        """Initialize the server runner.

        Args:
            client_endpoint (int | str):
                Port for client connections, or pathname for IPC socket if
                use_ipc is True.
            engine_zmq_endpoint (str):
                Address for cognitive engine connections.
            num_tokens (int):
                Number of tokens for flow control.
            input_queue_maxsize (int):
                Maximum size of input queue for each cognitive engine.
            timeout (int):
                Timeout in seconds for cognitive engine heartbeats.
            message_max_size (int, optional):
                Maximum size of messages from clients in bytes. Only applies to
                websocket connections.
            use_zeromq (bool):
                Whether to use ZeroMQ for client connections instead of
                websockets.
            prometheus_port (int):
                Port for Prometheus metrics.
            use_ipc (bool):
                Whether to use IPC for client connections instead of TCP. If
                this is True, use_zeromq must also be True.
        """
        self.client_endpoint = client_endpoint
        self.engine_zmq_endpoint = engine_zmq_endpoint
        self.num_tokens = num_tokens
        self.input_queue_maxsize = input_queue_maxsize
        self.timeout = timeout
        self.message_max_size = message_max_size
        self.use_zeromq = use_zeromq
        self.prometheus_port = prometheus_port
        self.use_ipc = use_ipc

    def run(self):
        """Run the Gabriel server."""
        asyncio.run(self.run_async())

    async def run_async(self):
        """Run the Gabriel server."""
        if self.use_ipc and not self.use_zeromq:
            raise ValueError("IPC can only be used with ZeroMQ")
        start_http_server(self.prometheus_port)
        context = zmq.asyncio.Context()
        zmq_socket = context.socket(zmq.ROUTER)
        zmq_socket.bind(self.engine_zmq_endpoint)
        logger.info(
            f"Waiting for engines to connect on {self.engine_zmq_endpoint}"
        )

        server = _Server(
            self.num_tokens,
            zmq_socket,
            self.timeout,
            self.input_queue_maxsize,
            self.use_zeromq,
            self.use_ipc,
        )
        self.server = server.get_server()
        try:
            await server.launch_async(
                self.client_endpoint, self.message_max_size
            )
        except:
            zmq_socket.close(0)
            context.destroy()
            raise

    def get_server(self):
        """Return the server instance."""
        return self.server


class _Server:
    def __init__(
        self,
        num_tokens,
        zmq_socket,
        timeout,
        size_for_queues,
        use_zeromq,
        use_ipc,
    ):
        self._zmq_socket = zmq_socket
        self._engine_workers = {}
        # Mapping from source id to source info
        self._source_infos: dict[str, _SourceInfo] = {}
        self._timeout = timeout
        self._size_for_queues = size_for_queues
        self._server = (ZeroMQServer if use_zeromq else WebsocketServer)(
            num_tokens, self._send_to_engine
        )
        self.use_ipc = use_ipc

    def launch(self, client_port, message_max_size):
        asyncio.run(self.launch_async(client_port, message_max_size))

    async def launch_async(self, client_port, message_max_size):
        async def receive_from_engine_worker_loop():
            await self._server.wait_for_start()
            while self._server.is_running():
                await self._receive_from_engine_worker_helper()
            logger.debug("Engine receiver loop shut down")

        async def heartbeat_loop():
            await self._server.wait_for_start()
            while self._server.is_running():
                await asyncio.sleep(self._timeout)
                await self._heartbeat_helper()
            logger.debug("Heartbeat loop shut down")

        engine_receiver_task = asyncio.create_task(
            receive_from_engine_worker_loop()
        )
        engine_heartbeat_task = asyncio.create_task(heartbeat_loop())

        server_task = self._server.launch_async(
            client_port, message_max_size, self.use_ipc
        )

        tasks = [engine_receiver_task, engine_heartbeat_task, server_task]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._zmq_socket.close(0)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        logger.info("Server shut down")

    def get_server(self):
        """Return the server instance."""
        return self._server

    async def _calculate_engine_metrics(self, engine_worker):
        processing_latency = (
            time.perf_counter() - engine_worker.get_last_payload_send_time()
        )
        logger.info(
            f"Engine {engine_worker.get_engine_name()} processing latency: "
            f"{processing_latency:.2f} seconds"
        )
        ENGINE_LATENCY.labels(engine=engine_worker.get_engine_name()).observe(
            processing_latency
        )

    async def _receive_from_engine_worker_helper(self):
        """Consume from ZeroMQ queue for cognitive engines messages."""
        logger.debug("Waiting for message from engine")
        try:
            address, _, payload = await asyncio.wait_for(
                self._zmq_socket.recv_multipart(), timeout=1
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.debug("No message from engine within timeout")
            return
        logger.debug(f"Received message from engine at address {address}")

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

        await self._calculate_engine_metrics(engine_worker)
        logger.debug(
            f"Received result from engine {engine_worker.get_engine_name()}"
        )
        engine_worker.set_last_received(time.time())

        result_wrapper = from_standalone_engine.result_wrapper
        engine_worker_metadata = engine_worker.get_current_input_metadata()
        source_info = self._source_infos.get(engine_worker_metadata.source_id)
        if source_info is None:
            logger.error("Source info not found")
            return

        # Check if the result corresponds to the latest input that was
        # available for this engine from this source
        latest_input = source_info.latest_input
        # Check if this engine is the first to finish processing this input
        if (
            latest_input is not None
            and latest_input.metadata == engine_worker_metadata
        ):
            # Send response to client
            logger.debug(
                f"Sending result from engine {engine_worker.get_engine_name()}"
                f" to client {engine_worker_metadata.client_address}"
            )
            await self._server.send_result_wrapper(
                engine_worker_metadata.client_address,
                source_info.get_name(),
                engine_worker_metadata.frame_id,
                engine_worker.get_engine_name(),
                result_wrapper,
                return_token=True,
            )

            # Send the next input to the engine from the queue
            await engine_worker.send_next_input()
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
            logger.debug(
                f"No new input for engine {engine_worker.get_engine_name()}"
            )
            engine_worker.clear_current_input_metadata()
        else:
            # Give the worker the latest input
            logger.debug(
                f"Sending latest input to engine "
                f"{engine_worker.get_engine_name()}"
            )
            await engine_worker.send_payload(latest_input)

    async def _add_engine_worker(self, address, from_standalone_engine):
        if not from_standalone_engine.HasField("welcome"):
            logger.warning(
                "Non-welcome message from unknown engine. Consider increasing "
                "timeout."
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
            time_since_last_received = (
                current_time - engine_worker.get_last_received()
            )
            if time_since_last_received < self._timeout:
                continue

            if (
                not engine_worker.get_awaiting_heartbeat_response()
            ) and engine_worker.get_current_input_metadata() is None:
                # Send a heartbeat since the engine is idle and we aren't
                # waiting for a heartbeat from this engine
                await engine_worker.send_heartbeat()
                # Update the last received time so we reset the timeout
                # countdown
                engine_worker.set_last_received(time.time())
                continue

            logger.info(
                f"Engine {engine_worker.get_engine_name()} offline for "
                f"{time_since_last_received:.2f} seconds"
            )

            logger.info(
                f"Lost connection to engine worker "
                f"{engine_worker.get_engine_name()}"
            )

            current_input_metadata = engine_worker.get_current_input_metadata()

            if current_input_metadata is None:
                logger.info("Engine disconnected while it was idle")
                del self._engine_workers[address]
                return

            source_info = self._source_infos.get(
                current_input_metadata.source_id
            )
            if source_info is None:
                logger.error("Source info not found")
                return

            latest_input = source_info.latest_input
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
        logger.debug(
            f"Received input from client {client_address} with source ID "
            f"{from_client.source_id} and frame id {from_client.frame_id}; "
            f"target engines: {from_client.target_engine_ids}"
        )
        if from_client.source_id not in self._source_infos:
            self._source_infos[from_client.source_id] = _SourceInfo(
                from_client.source_id,
                self._engine_workers,
                self._size_for_queues,
            )
        source_info = self._source_infos[from_client.source_id]
        return await source_info.process_input_from_client(
            from_client, client_address
        )


class _EngineWorker:
    """Information about a cognitive engine worker.

    A cognitive enginer worker processes inputs from clients.
    """

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
        # Last time a message was sent to the engine, including heartbeats
        self._last_received = 0
        self._last_payload_send_time = 0
        self._awaiting_heartbeat_response = False
        self._current_input_metadata = None
        # Maximum size for each source queue
        self._size_for_queues = fresh_inputs_queue_size
        self._sources = deque()

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
        logger.debug(f"Received heartbeat from engine {self._engine_name}")
        self._awaiting_heartbeat_response = False
        self._last_received = time.time()

    def get_awaiting_heartbeat_response(self):
        return self._awaiting_heartbeat_response

    def get_last_received(self):
        return self._last_received

    def get_last_payload_send_time(self):
        return self._last_payload_send_time

    def set_last_received(self, time):
        self._last_received = time

    async def send_heartbeat(self):
        await self._send_helper(network_engine.HEARTBEAT, heartbeat=True)
        self._awaiting_heartbeat_response = True

    async def _send_helper(self, payload, heartbeat=False):
        """Send the payload to the cognitive engine."""
        await self._zmq_socket.send_multipart([self._address, b"", payload])
        if not heartbeat:
            self._last_payload_send_time = time.perf_counter()
            logger.debug(f"Sent payload to engine {self._engine_name}")
        else:
            logger.debug(f"Sent heartbeat to engine {self._engine_name}")

    async def send_payload(self, metadata_payload):
        self._current_input_metadata = metadata_payload.metadata
        await self._send_helper(metadata_payload.payload, heartbeat=False)

    async def send_next_input(self):
        for _ in range(len(self._sources)):
            source_info = self._sources.popleft()
            self._sources.append(source_info)
            metadata_payload = await source_info.get_input_from_queue(
                self._engine_name
            )
            if metadata_payload is not None:
                await self.send_payload(metadata_payload)
            else:
                self.clear_current_input_metadata()
        return None

    async def add_source(self, source_info):
        if source_info in self._sources:
            return
        self._sources.append(source_info)

    async def remove_source(self, source_info):
        if source_info in self._sources:
            self._sources.remove(source_info)


class _SourceInfo:
    """Information about a client input producer.

    A client input producer is a source of input for a set of cognitive
    engines.
    """

    def __init__(self, source_id, engine_workers, size_for_queues):
        self._source_id = source_id
        self._engine_workers = engine_workers
        self._last_input_arrival_time = None
        self._input_queue = deque(maxlen=size_for_queues)
        self._size_for_queues = size_for_queues
        self.latest_input = None
        self.target_engines = None

    def get_name(self):
        return self._source_id

    async def process_input_from_client(
        self, from_client: gabriel_pb2.FromClient, client_address: str
    ):
        """Process input received from a client.

        Send it to the targetted engine workers.

        Args:
            from_client: The client input to process.
            client_address: The address of the client.
        """
        logger.debug(
            f"Processing input from client {client_address} with source ID "
            f"{from_client.source_id} and frame id {from_client.frame_id}; "
            f"target engines: {from_client.target_engine_ids}"
        )

        if self._last_input_arrival_time is None:
            self._last_input_arrival_time = time.perf_counter()
        else:
            current_time = time.perf_counter()
            inter_arrival_time = current_time - self._last_input_arrival_time
            INPUT_INTER_ARRIVAL_TIME.labels(source_id=self._source_id).observe(
                inter_arrival_time
            )
            self._last_input_arrival_time = current_time

        metadata = Metadata(
            frame_id=from_client.frame_id,
            source_id=self._source_id,
            client_address=client_address,
            target_engine_ids=from_client.target_engine_ids,
        )
        payload = from_client.input_frame.SerializeToString()
        metadata_payload = MetadataPayload(metadata=metadata, payload=payload)

        target_engines = set()
        for engine_worker in self._engine_workers.values():
            if (
                engine_worker.get_engine_name()
                in from_client.target_engine_ids
            ):
                target_engines.add(engine_worker)

        if not target_engines:
            # TODO: better error handling
            logger.error(
                f"No target engines found for {self._engine_workers.values()}"
            )
            return False

        # Remove this source from any engines that are no longer targetted
        if self.target_engines != target_engines:
            removed_targets = (
                self.target_engines - target_engines
                if self.target_engines
                else set()
            )
            for engine in removed_targets:
                engine_worker = self._engine_workers.get(engine)
                if engine_worker:
                    engine_worker.remove_source(self)
            self.target_engines = target_engines

        logger.debug(
            f"Targetting engines "
            f"{[e.get_engine_name() for e in target_engines]}"
        )

        all_engines_busy = True
        for engine_worker in set(target_engines):
            await engine_worker.add_source(self)
            # If the engine is idle, send the input immediately
            if engine_worker.get_current_input_metadata() is None:
                all_engines_busy = False
                await engine_worker.send_payload(metadata_payload)

        if all_engines_busy:
            await self.add_input_to_queue(metadata_payload)
            return True

        self.latest_input = metadata_payload
        return True

    async def add_input_to_queue(self, metadata_payload):
        # Add input to the queue, dropping the oldest input if the queue is
        # full
        self._input_queue.append(metadata_payload)
        SOURCE_QUEUE_LENGTH.labels(source_id=self._source_id).set(
            len(self._input_queue)
        )

    async def get_input_from_queue(self, engine_name):
        logger.debug(f"Getting input from queue for engine {engine_name}")
        if not self._input_queue:
            logger.debug("Input queue is empty")
            self.latest_input = None
            return None
        metadata_payload = self._input_queue[0]
        self.latest_input = metadata_payload
        return self._input_queue.popleft()
