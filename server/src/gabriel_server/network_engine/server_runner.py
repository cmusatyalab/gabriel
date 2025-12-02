"""Run the Gabriel server that connects clients to cognitive engines."""

import asyncio
import logging
import time
from collections import deque, namedtuple
from typing import Optional, Union

import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2
from gabriel_protocol.gabriel_pb2 import StatusCode
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from gabriel_server import network_engine
from gabriel_server.websocket_server import WebsocketServer
from gabriel_server.zeromq_server import ZeroMQServer

FIVE_SECONDS = 5

logger = logging.getLogger(__name__)


Metadata = namedtuple(
    "Metadata",
    ["frame_id", "producer_id", "client_address", "target_engine_ids"],
)


MetadataPayload = namedtuple("MetadataPayload", ["metadata", "payload"])

ENGINE_LATENCY = Histogram(
    "gabriel_engine_processing_latency_seconds",
    "End-to-end engine processing latency",
    ["engine_id"],
)

PRODUCER_QUEUE_LENGTH = Gauge(
    "gabriel_producer_queue_length",
    "Length of each producer queue",
    ["producer_id"],
)

CLIENT_INPUTS_RECEIVED_TOTAL = Counter(
    "gabriel_producer_inputs_received_total",
    "Total number of client inputs received by the Gabriel server from a "
    "producer",
    ["producer_id"],
)

ENGINE_INPUTS_RECEIVED_TOTAL = Counter(
    "gabriel_engine_inputs_received_total",
    "Total number of client inputs received that target an engine",
    ["engine_id"],
)

ENGINE_INPUTS_PROCESSED_TOTAL = Counter(
    "gabriel_engine_inputs_processed_total",
    "Total number of inputs processed by an engine",
    ["engine_id"],
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
        zmq_socket.setsockopt(zmq.LINGER, 0)
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
        except Exception as e:
            logger.error(e)
            zmq_socket.close()
            context.term()
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
        self._engine_ids = set()
        # Mapping from producer id to producer info
        self._producer_infos: dict[str, _ProducerInfo] = {}
        self._timeout = timeout
        self._size_for_queues = size_for_queues
        self._server = (ZeroMQServer if use_zeromq else WebsocketServer)(
            num_tokens, self._send_to_engine, self._engine_ids
        )
        self.use_ipc = use_ipc

    def launch(self, client_port, message_max_size):
        asyncio.run(self.launch_async(client_port, message_max_size))

    async def launch_async(self, client_port, message_max_size):
        async def receive_from_engine_worker_loop():
            await self._server.wait_for_start()
            while self._server.is_running():
                await self._receive_from_engine_worker_helper()
            logger.info("Engine receiver loop shut down")

        async def heartbeat_loop():
            await self._server.wait_for_start()
            while self._server.is_running():
                await asyncio.sleep(0.1)
                await self._heartbeat_helper()
            logger.info("Heartbeat loop shut down")

        engine_receiver_task = asyncio.create_task(
            receive_from_engine_worker_loop()
        )
        engine_heartbeat_task = asyncio.create_task(heartbeat_loop())

        server_task = asyncio.create_task(
            self._server.launch_async(
                client_port, message_max_size, self.use_ipc
            )
        )

        tasks = [engine_receiver_task, engine_heartbeat_task, server_task]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._zmq_socket.close()
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
            f"Engine {engine_worker.get_engine_id()} processing latency: "
            f"{processing_latency:.2f} seconds"
        )
        ENGINE_LATENCY.labels(engine_id=engine_worker.get_engine_id()).observe(
            processing_latency
        )

    async def _receive_from_engine_worker_helper(self):
        """Consume from ZeroMQ queue for cognitive engines messages."""
        if await self._zmq_socket.poll(timeout=1000) == 0:
            return
        address, _, payload = await self._zmq_socket.recv_multipart()

        logger.debug(f"Received message from engine {address}")

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
            f"Received result from engine {engine_worker.get_engine_id()}"
        )
        engine_worker.set_last_received(time.monotonic())

        ENGINE_INPUTS_PROCESSED_TOTAL.labels(
            engine_id=engine_worker.get_engine_id()
        ).inc()

        result = from_standalone_engine.result
        engine_worker_metadata = engine_worker.get_current_input_metadata()
        producer_info = self._producer_infos.get(
            engine_worker_metadata.producer_id
        )
        if producer_info is None:
            logger.error("Producer info not found")
            return

        # Check if the result corresponds to the latest input that was
        # available for this engine from this producer
        latest_input = producer_info.latest_input_sent_to_engine
        # Check if this engine is the first to finish processing the latest
        # input. If so, it should get the next input from the queue.
        if (
            latest_input is not None
            and latest_input.metadata == engine_worker_metadata
        ):
            # Send response to client
            logger.debug(
                f"Sending result from engine {engine_worker.get_engine_id()}"
                f" to client {engine_worker_metadata.client_address}"
            )
            await self._server.send_result(
                engine_worker_metadata.client_address,
                producer_info.get_name(),
                engine_worker.get_engine_id(),
                result,
                return_token=True,
            )

            # Send the next input to the engine from the queue
            await engine_worker.send_next_input()
            return

        if engine_worker.get_all_responses_required():
            await self._server.send_result(
                engine_worker_metadata.client_address,
                producer_info.get_name(),
                engine_worker.get_engine_id(),
                result,
                return_token=False,
            )

        if latest_input is None:
            # There is no new input to give the worker
            logger.debug(
                f"No new input for engine {engine_worker.get_engine_id()}"
            )
            engine_worker.clear_current_input_metadata()
        else:
            # Give the worker the latest input
            logger.debug(
                f"Sending latest input to engine "
                f"{engine_worker.get_engine_id()}"
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
        engine_id = welcome.engine_id

        # An engine with this id is already connected, remove that engine
        # worker from the server
        if engine_id in self._engine_ids:
            logger.warning(f"Engine with id {engine_id} is already connected!")
            for address, worker in list(self._engine_workers.items()):
                if worker.get_engine_id() == engine_id:
                    await self._remove_engine_worker(address)
                    break

        logger.info(f"New engine {engine_id} connected")

        all_responses_required = welcome.all_responses_required
        engine_worker = _EngineWorker(
            self._zmq_socket,
            address,
            engine_id,
            all_responses_required,
            self._size_for_queues,
        )
        self._engine_workers[address] = engine_worker
        self._engine_ids.add(engine_id)
        await self._server._engines_updated_cb()

    async def _remove_engine_worker(self, address):
        """Remove an engine worker."""
        engine_id = self._engine_workers[address].get_engine_id()
        self._engine_ids.remove(engine_id)
        del self._engine_workers[address]
        await self._server._engines_updated_cb()

    async def _heartbeat_helper(self):
        # We cannot directly iterate over items because we delete some entries
        for address, engine_worker in list(self._engine_workers.items()):
            last_received = engine_worker.get_last_received()
            time_since_last_received = time.monotonic() - last_received
            if time_since_last_received < self._timeout:
                await asyncio.sleep(0)
                continue

            # If the engine is inactive, send a heartbeat.
            if (
                not engine_worker.get_awaiting_heartbeat_response()
                and engine_worker.get_current_input_metadata() is None
            ):
                # Send a heartbeat since the engine is idle and we aren't
                # waiting for a heartbeat from this engine
                await engine_worker.send_heartbeat()
                # Update the last received time so we reset the timeout
                # countdown
                engine_worker.set_last_received(time.monotonic())
                continue

            engine_id = engine_worker.get_engine_id()

            logger.info(
                f"Engine {engine_id} offline for "
                f"{time_since_last_received:.2f} seconds"
            )

            logger.info(f"Lost connection to engine worker {engine_id}")

            ENGINE_INPUTS_RECEIVED_TOTAL.remove(engine_id)
            ENGINE_INPUTS_PROCESSED_TOTAL.remove(engine_id)

            current_input_metadata = engine_worker.get_current_input_metadata()

            if current_input_metadata is None:
                logger.info("Engine disconnected while it was idle")
                await self._remove_engine_worker(address)
                continue

            producer_info = self._producer_infos.get(
                current_input_metadata.producer_id
            )
            if producer_info is None:
                logger.error("Source info not found")
                await self._remove_engine_worker(address)
                continue

            latest_input = producer_info.latest_input_sent_to_engine
            if (
                latest_input is not None
                and current_input_metadata == latest_input.metadata
            ):
                # Return token for frame engine was in the middle of processing
                result = gabriel_pb2.Result()
                result.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
                result.status.message = f"Engine {engine_id} disconnected"
                result.target_engine_id = engine_id
                result.frame_id = current_input_metadata.frame_id

                # TODO(Aditya): what about other targeted engines?

                await self._server.send_result(
                    current_input_metadata.client_address,
                    producer_info.get_name(),
                    engine_worker.get_engine_id(),
                    result,
                    return_token=True,
                )
            await self._remove_engine_worker(address)

    async def _send_to_engine(self, from_client, client_address):
        logger.debug(
            f"Received input from client {client_address} with source ID "
            f"{from_client.producer_id} and frame id {from_client.frame_id}; "
            f"target engines: {from_client.target_engine_ids}"
        )
        if from_client.producer_id not in self._producer_infos:
            self._producer_infos[from_client.producer_id] = _ProducerInfo(
                from_client.producer_id,
                self._engine_workers,
                self._size_for_queues,
            )
        producer_info = self._producer_infos[from_client.producer_id]
        return await producer_info.process_input_from_client(
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
        engine_id,
        all_responses_required,
        fresh_inputs_queue_size,
    ):
        self._zmq_socket = zmq_socket
        self._address = address
        self._engine_id = engine_id
        self._all_responses_required = all_responses_required
        # Last time a message was sent to the engine, including heartbeats
        self._last_received = time.monotonic()
        self._last_payload_send_time = 0
        self._awaiting_heartbeat_response = False
        self._current_input_metadata = None
        # Maximum size for each source queue
        self._size_for_queues = fresh_inputs_queue_size
        self._producers = deque()

    def get_address(self):
        return self._address

    def get_engine_id(self):
        return self._engine_id

    def get_current_input_metadata(self):
        return self._current_input_metadata

    def get_all_responses_required(self):
        return self._all_responses_required

    def clear_current_input_metadata(self):
        self._current_input_metadata = None

    def record_heatbeat(self):
        logger.debug(f"Received heartbeat from engine {self._engine_id}")
        self._awaiting_heartbeat_response = False
        self._last_received = time.monotonic()

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
            logger.debug(f"Sent payload to engine {self._engine_id}")
        else:
            logger.debug(f"Sent heartbeat to engine {self._engine_id}")

    async def send_payload(self, metadata_payload):
        self._current_input_metadata = metadata_payload.metadata
        await self._send_helper(metadata_payload.payload, heartbeat=False)

    async def send_next_input(self):
        """Send next input from queue."""
        for _ in range(len(self._producers)):
            producer_info = self._producers.popleft()
            self._producers.append(producer_info)
            metadata_payload = await producer_info.get_input_from_queue(
                self._engine_id
            )
            if metadata_payload is not None:
                await self.send_payload(metadata_payload)
                return
        self.clear_current_input_metadata()

    async def add_producer(self, producer_info):
        if producer_info in self._producers:
            return
        self._producers.append(producer_info)

    async def remove_producer(self, producer_info):
        if producer_info in self._producers:
            self._producers.remove(producer_info)


class _ProducerInfo:
    """Information about a client input producer.

    A client input producer is a source of input for a set of cognitive
    engines.
    """

    def __init__(self, producer_id, engine_workers, size_for_queues):
        self._producer_id = producer_id
        self._engine_workers = engine_workers
        self._input_queue = deque(maxlen=size_for_queues)
        self._size_for_queues = size_for_queues
        # The latest input from this source that was sent to at least one
        # engine.
        self.latest_input_sent_to_engine = None
        self.target_engines = None

    def get_name(self):
        return self._producer_id

    async def process_input_from_client(
        self, from_client: gabriel_pb2.FromClient, client_address: str
    ):
        """Process input received from a client.

        Send it to the targeted engine workers.

        Args:
            from_client: The client input to process.
            client_address: The address of the client.
        """
        logger.debug(
            f"Processing input from client {client_address} with source ID "
            f"{from_client.producer_id} and frame id {from_client.frame_id}; "
            f"target engines: {from_client.target_engine_ids}"
        )

        CLIENT_INPUTS_RECEIVED_TOTAL.labels(
            producer_id=self._producer_id
        ).inc()

        metadata = Metadata(
            frame_id=from_client.frame_id,
            producer_id=self._producer_id,
            client_address=client_address,
            target_engine_ids=from_client.target_engine_ids,
        )
        payload = from_client.SerializeToString()
        metadata_payload = MetadataPayload(metadata=metadata, payload=payload)

        target_engines = set()
        for engine_worker in self._engine_workers.values():
            if engine_worker.get_engine_id() in from_client.target_engine_ids:
                target_engines.add(engine_worker)
                ENGINE_INPUTS_RECEIVED_TOTAL.labels(
                    engine_id=engine_worker.get_engine_id()
                ).inc()

        if not target_engines:
            available_engine_ids = [
                worker.get_engine_id()
                for worker in self._engine_workers.values()
            ]

            # TODO: better error handling
            logger.error(
                f"No target engines found for {from_client.target_engine_ids};"
                f" {available_engine_ids=}"
            )
            return (
                StatusCode.NO_ENGINE_FOR_INPUT,
                f"No target engines found. Specified target: "
                f"{from_client.target_engine_ids}. Available engines: "
                f"{available_engine_ids}",
            )

        # Remove this source from any engines that are no longer targeted
        if self.target_engines != target_engines:
            removed_targets = (
                self.target_engines - target_engines
                if self.target_engines
                else set()
            )
            for engine in removed_targets:
                engine_worker = self._engine_workers.get(engine)
                if engine_worker:
                    engine_worker.remove_producer(self)
            self.target_engines = target_engines

        logger.debug(
            f"Targeting engines {[e.get_engine_id() for e in target_engines]}"
        )

        all_engines_busy = True
        for engine_worker in set(target_engines):
            await engine_worker.add_producer(self)
            # If the engine is idle, send the input immediately
            if engine_worker.get_current_input_metadata() is None:
                all_engines_busy = False
                await engine_worker.send_payload(metadata_payload)

        if all_engines_busy:
            success = await self.add_input_to_queue(metadata_payload)
            if success:
                return (StatusCode.SUCCESS, "")
            return (
                StatusCode.SERVER_DROPPED_FRAME,
                f"Input queue for {self._producer_id} is full, dropping input",
            )

        # Latest input is only set if the input was sent to at least one
        # engine.
        self.latest_input_sent_to_engine = metadata_payload
        return (StatusCode.SUCCESS, "")

    async def add_input_to_queue(self, metadata_payload):
        # Add input to the queue if it is not full
        if len(self._input_queue) == self._input_queue.maxlen:
            logger.warning(
                f"Input queue for {self._producer_id} is full, dropping input"
            )
            return False
        self._input_queue.append(metadata_payload)
        PRODUCER_QUEUE_LENGTH.labels(producer_id=self._producer_id).set(
            len(self._input_queue)
        )
        return True

    async def get_input_from_queue(self, engine_id):
        logger.debug(
            f"Getting input from queue for engine {engine_id} and producer id "
            f"{self._producer_id}"
        )
        if not self._input_queue:
            logger.debug(
                f"Input queue is empty for producer id {self._producer_id}"
            )
            self.latest_input_sent_to_engine = None
            return None
        metadata_payload = self._input_queue[0]
        self.latest_input_sent_to_engine = metadata_payload
        return self._input_queue.popleft()
