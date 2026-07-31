"""Run the Gabriel server that connects clients to cognitive engines."""

import asyncio
import enum
import logging
import time
from collections import deque, namedtuple
from typing import Optional, Union

import grpc
from gabriel_protocol import gabriel_pb2, gabriel_pb2_grpc
from gabriel_protocol.gabriel_pb2 import StatusCode
from gabriel_protocol.tls_utils import build_server_credentials
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from gabriel_server.grpc_server import GrpcServer
from gabriel_server.websocket_server import WebsocketServer
from gabriel_server.zeromq_server import ZeroMQServer

FIVE_SECONDS = 5
ENGINE_SERVER_STOP_GRACE_SECONDS = 1

# Must permit pings at least as often as the engine's
# engine_runner.KEEPALIVE_TIME_MS, or gRPC will kill the connection for
# "too_many_pings" instead of letting the keepalive do its job.
ENGINE_KEEPALIVE_MIN_PING_INTERVAL_MS = 5_000

# The server also pings each engine itself, so a silently dead engine (e.g.
# a crashed process or network partition, as opposed to a clean stream
# close) is still noticed and cleaned up, mirroring what the app-level
# heartbeat used to do.
ENGINE_KEEPALIVE_TIME_MS = 10_000
ENGINE_KEEPALIVE_TIMEOUT_MS = 5_000

logger = logging.getLogger(__name__)


class Transport(enum.Enum):
    """The transport used for client connections."""

    ZEROMQ = "zeromq"
    WEBSOCKET = "websocket"
    GRPC = "grpc"


_TRANSPORT_CLASSES = {
    Transport.ZEROMQ: ZeroMQServer,
    Transport.WEBSOCKET: WebsocketServer,
    Transport.GRPC: GrpcServer,
}


_Metadata = namedtuple(
    "_Metadata",
    [
        "frame_id",
        "producer_id",
        "client_address",
        "target_engine_ids",
        "client_info",
    ],
)


_MetadataPayload = namedtuple("_MetadataPayload", ["metadata", "payload"])

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
        engine_endpoint: Union[int, str],
        num_tokens: int,
        input_queue_maxsize: int,
        message_max_size: Optional[int] = None,
        client_transport: Transport = Transport.GRPC,
        prometheus_port: int = 8000,
        use_client_ipc: bool = False,
        use_engine_ipc: bool = False,
        tls_cert: Optional[str] = None,
        tls_key: Optional[str] = None,
        tls_client_ca_cert: Optional[str] = None,
    ):
        """Initialize the server runner.

        Args:
            client_endpoint (int | str):
                Port for client connections, or pathname for a Unix domain
                socket if use_client_ipc is True.
            engine_endpoint (int | str):
                Port for cognitive engine connections over gRPC, or pathname
                for a Unix domain socket if use_engine_ipc is True.
            num_tokens (int):
                Number of tokens for flow control.
            input_queue_maxsize (int):
                Maximum size of input queue for each cognitive engine.
            message_max_size (int, optional):
                Maximum size of messages from clients in bytes. Only applies to
                websocket connections.
            client_transport (Transport):
                Which transport to use for client connections.
            prometheus_port (int):
                Port for Prometheus metrics.
            use_client_ipc (bool):
                Whether to use a Unix domain socket for client connections
                instead of TCP.
            use_engine_ipc (bool):
                Whether to use a Unix domain socket for engine connections
                instead of TCP. Only sensible when engines run on the same
                host as the server.
            tls_cert (str, optional):
                Path to a PEM certificate chain for the gRPC servers (both
                the client-facing one, if `client_transport` is
                `Transport.GRPC`, and the engine-facing one) to present. If
                either tls_cert or tls_key is omitted, gRPC servers listen
                on insecure (plaintext) ports.
            tls_key (str, optional):
                Path to a PEM private key for the gRPC servers (both the
                client-facing one, if `client_transport` is
                `Transport.GRPC`, and the engine-facing one) to present. If
                either tls_cert or tls_key is omitted, gRPC servers listen
                on insecure (plaintext) ports.
            tls_client_ca_cert (str, optional):
                Path to a PEM CA certificate used to verify client/engine
                certificates. Providing this enables mutual TLS.
        """
        self.client_endpoint = client_endpoint
        self.engine_endpoint = engine_endpoint
        self.num_tokens = num_tokens
        self.input_queue_maxsize = input_queue_maxsize
        self.message_max_size = message_max_size
        self.client_transport = client_transport
        self.prometheus_port = prometheus_port
        self.use_client_ipc = use_client_ipc
        self.use_engine_ipc = use_engine_ipc
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.tls_client_ca_cert = tls_client_ca_cert

    def run(self):
        """Run the Gabriel server."""
        asyncio.run(self.run_async())

    async def run_async(self):
        """Run the Gabriel server."""
        # start_http_server spawns a daemon thread running its own
        # serve_forever() loop. It gives us no way to stop it unless we
        # hang onto the returned server/thread ourselves, so make sure to
        # shut it down in the finally block below instead
        prometheus_httpd, prometheus_thread = start_http_server(
            self.prometheus_port
        )

        server = _Server(
            self.num_tokens,
            self.engine_endpoint,
            self.input_queue_maxsize,
            self.client_transport,
            self.use_client_ipc,
            self.use_engine_ipc,
            self.tls_cert,
            self.tls_key,
            self.tls_client_ca_cert,
        )
        self.server = server.server
        try:
            await server.launch_async(
                self.client_endpoint, self.message_max_size
            )
        finally:

            def shutdown_prometheus():
                prometheus_httpd.shutdown()
                prometheus_httpd.server_close()
                prometheus_thread.join()

            await asyncio.to_thread(shutdown_prometheus)


class _Server(gabriel_pb2_grpc.GabrielEngineServiceServicer):
    def __init__(
        self,
        num_tokens,
        engine_endpoint,
        size_for_queues,
        client_transport,
        use_client_ipc,
        use_engine_ipc,
        tls_cert=None,
        tls_key=None,
        tls_client_ca_cert=None,
    ):
        self._engine_endpoint = engine_endpoint
        self._use_engine_ipc = use_engine_ipc
        self._engine_workers = {}
        self._engine_ids = set()
        # Mapping from producer id to producer info
        self._producer_infos: dict[str, _ProducerInfo] = {}
        self._size_for_queues = size_for_queues
        self._tls_cert = tls_cert
        self._tls_key = tls_key
        self._tls_client_ca_cert = tls_client_ca_cert
        transport_kwargs = {}
        if client_transport == Transport.GRPC:
            transport_kwargs = {
                "tls_cert": tls_cert,
                "tls_key": tls_key,
                "tls_client_ca_cert": tls_client_ca_cert,
            }
        # The server used to service Gabriel clients
        self.server = _TRANSPORT_CLASSES[client_transport](
            num_tokens,
            self._send_to_engine,
            self._engine_ids,
            **transport_kwargs,
        )
        self.client_transport = client_transport
        self.use_client_ipc = use_client_ipc
        self._engine_grpc_server = None

    def launch(self, client_port, message_max_size):
        asyncio.run(self.launch_async(client_port, message_max_size))

    async def launch_async(self, client_port, message_max_size):
        async def log_connected_engines():
            await self.server.wait_for_start()
            while self.server.is_running():
                await asyncio.sleep(10)
                logger.info(f"Connected engines: {self._engine_ids}")

        options = [
            # Permit the engine's own keepalive pings (see engine_runner.py's
            # KEEPALIVE_TIME_MS) on an otherwise idle stream, so the server
            # doesn't kill the connection for "too_many_pings".
            (
                "grpc.http2.min_ping_interval_without_data_ms",
                ENGINE_KEEPALIVE_MIN_PING_INTERVAL_MS,
            ),
            ("grpc.http2.max_pings_without_data", 0),
            # Also ping each engine from the server side, so a silently dead
            # engine is noticed even if it never sends another ping itself.
            # Either direction's failed ping tears down the RPC, which
            # EngineSession's finally block already handles.
            ("grpc.keepalive_time_ms", ENGINE_KEEPALIVE_TIME_MS),
            ("grpc.keepalive_timeout_ms", ENGINE_KEEPALIVE_TIMEOUT_MS),
            ("grpc.keepalive_permit_without_calls", 1),
        ]
        if message_max_size is not None:
            options.append(("grpc.max_send_message_length", message_max_size))
            options.append(
                ("grpc.max_receive_message_length", message_max_size)
            )

        self._engine_grpc_server = grpc.aio.server(options=options)
        gabriel_pb2_grpc.add_GabrielEngineServiceServicer_to_server(
            self, self._engine_grpc_server
        )
        target = (
            f"unix://{self._engine_endpoint}"
            if self._use_engine_ipc
            else f"[::]:{self._engine_endpoint}"
        )
        credentials = build_server_credentials(
            self._tls_cert, self._tls_key, self._tls_client_ca_cert
        )
        if credentials is not None:
            self._engine_grpc_server.add_secure_port(target, credentials)
        else:
            self._engine_grpc_server.add_insecure_port(target)

        await self._engine_grpc_server.start()
        logger.info(
            f"Waiting for engines to connect on {self._engine_endpoint}"
        )

        server_task = asyncio.create_task(
            self.server.launch_async(
                client_port, message_max_size, self.use_client_ipc
            )
        )

        log_engines_task = asyncio.create_task(log_connected_engines())

        tasks = [log_engines_task, server_task]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            # When the gather() await itself is cancelled (as opposed to one
            # of the tasks raising its own exception below), asyncio has
            # already delivered that cancellation to every task in `tasks`
            # as part of cancelling the gather - cancelling them again here
            # would interrupt a task's cleanup code a second time, mid-flight.
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            await self._engine_grpc_server.stop(
                grace=ENGINE_SERVER_STOP_GRACE_SECONDS
            )
        logger.info("Server shut down")

    async def EngineSession(self, request_iterator, context):  # noqa: N802
        """Handle a cognitive engine's stream for its entire lifetime.

        Invoked directly by the gRPC framework once per engine connection. The
        first message on the stream must be a `Register` message.
        """
        engine_iter = request_iterator.__aiter__()
        try:
            first = await engine_iter.__anext__()
        except StopAsyncIteration:
            return

        if not first.HasField("register"):
            logger.warning(
                "First message from engine was not a register message. "
                "Consider increasing timeout."
            )
            return

        await self._add_engine_worker(context, first.register)

        try:
            async for from_engine in engine_iter:
                await self._handle_from_engine(context, from_engine)
        finally:
            if context in self._engine_workers:
                engine_id = self._engine_workers[context].get_engine_id()
                logger.info(f"Engine {engine_id} stream closed")
                await self._remove_engine_worker(context)

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

    async def _handle_from_engine(self, context, from_engine):
        """Handle a single message received from a cognitive engine."""
        engine_worker = self._engine_workers.get(context)
        if engine_worker is None:
            logger.error("Message from unregistered engine")
            return

        if from_engine.HasField("register"):
            logger.error("Engine sent duplicate register message")
            return

        await self._calculate_engine_metrics(engine_worker)
        logger.debug(
            f"Received result from engine {engine_worker.get_engine_id()}"
        )

        ENGINE_INPUTS_PROCESSED_TOTAL.labels(
            engine_id=engine_worker.get_engine_id()
        ).inc()

        result = from_engine.result

        engine_worker_metadata = engine_worker.get_current_input_metadata()
        if engine_worker_metadata is not None:
            result.frame_id = engine_worker_metadata.frame_id

        # Pass the result to the result manager for sending to any result sinks
        await self.server.result_manager.process_result(result)

        if engine_worker_metadata is None:
            logger.error("No input metadata found for engine result")
            return

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
            producer_info.pending_token_return
            and latest_input.metadata == engine_worker_metadata
        ):
            # Send response to client
            logger.debug(
                f"Sending result from engine {engine_worker.get_engine_id()}"
                f" to client {engine_worker_metadata.client_address}"
            )
            producer_info.pending_token_return = False
            await self.server.send_result(
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
            await self.server.send_result(
                engine_worker_metadata.client_address,
                producer_info.get_name(),
                engine_worker.get_engine_id(),
                result,
                return_token=False,
            )
        await engine_worker.send_next_input()

    async def _add_engine_worker(self, context, register):
        engine_id = register.engine_id

        # An engine with this id is already connected, remove that engine
        # worker from the server
        if engine_id in self._engine_ids:
            logger.warning(f"Engine with id {engine_id} is already connected!")
            for existing_context, worker in list(self._engine_workers.items()):
                if worker.get_engine_id() == engine_id:
                    await self._remove_engine_worker(existing_context)
                    break

        logger.info(f"New engine {engine_id} connected")

        engine_worker = _EngineWorker(
            context,
            engine_id,
            register.all_responses_required,
            self._size_for_queues,
        )
        self._engine_workers[context] = engine_worker
        self._engine_ids.add(engine_id)
        await self.server._engines_updated_cb()

    async def _remove_engine_worker(self, context):
        """Remove an engine worker once it is disconnected.

        Cleans up metrics and, if the engine was in the middle of processing a
        frame when it disconnected, returns a token for that frame to the
        client so it isn't left waiting forever.
        """
        engine_worker = self._engine_workers[context]
        engine_id = engine_worker.get_engine_id()

        ENGINE_INPUTS_RECEIVED_TOTAL.remove(engine_id)
        ENGINE_INPUTS_PROCESSED_TOTAL.remove(engine_id)

        current_input_metadata = engine_worker.get_current_input_metadata()
        if current_input_metadata is not None:
            producer_info = self._producer_infos.get(
                current_input_metadata.producer_id
            )
            if producer_info is None:
                logger.error("Source info not found")
            else:
                latest_input = producer_info.latest_input_sent_to_engine
                if (
                    latest_input is not None
                    and current_input_metadata == latest_input.metadata
                    and (
                        producer_info.pending_token_return
                        or engine_worker.get_all_responses_required()
                    )
                ):
                    return_token = producer_info.pending_token_return
                    # Clear the flag first so that other engines targeted by
                    # the same input don't also return a token for it if
                    # they disconnect too.
                    producer_info.pending_token_return = False

                    result = gabriel_pb2.Result()
                    result.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
                    result.status.message = f"Engine {engine_id} disconnected"
                    result.target_engine_id = engine_id
                    result.frame_id = current_input_metadata.frame_id

                    await self.server.send_result(
                        current_input_metadata.client_address,
                        producer_info.get_name(),
                        engine_id,
                        result,
                        return_token=return_token,
                    )

        self._engine_ids.remove(engine_id)
        del self._engine_workers[context]
        await self.server._engines_updated_cb()

    async def _send_to_engine(self, from_client, client_address, client_info):
        logger.debug(
            f"Received input from client {client_address} with source ID "
            f"{from_client.input.producer_id} and frame id "
            f"{from_client.input.frame_id}; target engines: "
            f"{from_client.input.target_engine_ids}"
        )
        if from_client.input.producer_id not in self._producer_infos:
            self._producer_infos[from_client.input.producer_id] = (
                _ProducerInfo(
                    from_client.input.producer_id,
                    self._engine_workers,
                    self._size_for_queues,
                )
            )
        producer_info = self._producer_infos[from_client.input.producer_id]
        return await producer_info.process_input_from_client(
            from_client, client_address, client_info
        )


class _EngineWorker:
    """Information about a cognitive engine worker.

    A cognitive enginer worker processes inputs from clients.
    """

    def __init__(
        self,
        context,
        engine_id,
        all_responses_required,
        fresh_inputs_queue_size,
    ):
        self._context = context
        self._engine_id = engine_id
        self._all_responses_required = all_responses_required
        self._last_payload_send_time = 0
        self._current_input_metadata = None
        # Maximum size for each source queue
        self._size_for_queues = fresh_inputs_queue_size
        self._producers = deque()

        # Latest input processed for each producer
        self._latest_input_processed = {}

    def get_engine_id(self):
        return self._engine_id

    def get_current_input_metadata(self):
        return self._current_input_metadata

    def get_all_responses_required(self):
        return self._all_responses_required

    def clear_current_input_metadata(self):
        self._current_input_metadata = None

    def get_last_payload_send_time(self):
        return self._last_payload_send_time

    async def _send_helper(self, to_engine):
        """Send the message to the cognitive engine."""
        await self._context.write(to_engine)
        self._last_payload_send_time = time.perf_counter()
        logger.debug(f"Sent payload to engine {self._engine_id}")

    async def send_payload(self, metadata_payload):
        metadata = metadata_payload.metadata
        self._current_input_metadata = metadata
        self._latest_input_processed[metadata.producer_id] = metadata
        to_engine = gabriel_pb2.ToEngine(
            input_frame=metadata_payload.payload,
            client_info=metadata.client_info,
        )
        await self._send_helper(to_engine)

    async def send_next_input(self):
        """Send this engine its next input, rotating fairly across producers.

        Producers are tried in round-robin order (self._producers.rotate),
        and returning as soon as one has something to send leaves the deque
        rotated for next time, so a busy producer can't monopolize this
        engine's attention.

        Each producer has at most one "in-flight" frame at a time: the frame
        most recently dispatched to any of its target engines, for which no
        engine has yet returned a result (producer.pending_token_return is
        True while it's in flight, producer.latest_input_sent_to_engine holds
        it). Whichever engine finishes the in-flight frame first returns its
        token, which clears pending_token_return and lets this engine dequeue
        the next frame - making that the new in-flight frame. Any engine that
        asks for work while a frame is still in flight doesn't pull from the
        queue at all; it just picks up that same in-flight frame, as long as
        it hasn't already been sent it. This is what lets a slower engine
        skip straight to the newest input instead of working through a
        backlog.
        """
        for _ in range(len(self._producers)):
            self._producers.rotate(-1)
            producer = self._producers[0]

            # If a token return is pending, that means no engine has returned
            # a result for the current input for this producer. So we cannot
            # get a new item from the queue yet.
            if not producer.pending_token_return:
                # Send the next input from the queue
                metadata_payload = await producer.get_input_from_queue(
                    self._engine_id
                )
                if metadata_payload is not None:
                    await self.send_payload(metadata_payload)
                    return

            # Send the latest available frame from this producer if we haven't
            # processed it yet.
            metadata_payload = producer.latest_input_sent_to_engine
            if metadata_payload is None:
                continue
            producer_id = producer._producer_id
            latest_processed_frame = self._latest_input_processed.get(
                producer_id, None
            )
            if (
                latest_processed_frame is not None
                and metadata_payload.metadata.frame_id
                > latest_processed_frame.frame_id
            ):
                await self.send_payload(metadata_payload)
                return

        # No input available
        self.clear_current_input_metadata()

    async def add_producer(self, producer_info):
        if producer_info in self._producers:
            return
        self._producers.append(producer_info)

    async def remove_producer(self, producer_info):
        if producer_info in self._producers:
            self._producers.remove(producer_info)
            del self._latest_input_processed[producer_info._producer_id]


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
        # The "in-flight" input: the latest input from this source that was
        # sent to at least one engine.
        self.latest_input_sent_to_engine = None
        self.target_engines = None

        # Whether the in-flight input above is still awaiting its token
        # return, i.e. no engine has returned a result for it yet. See
        # _EngineWorker.send_next_input for how this gates the input queue.
        self.pending_token_return = None

    def get_name(self):
        return self._producer_id

    async def process_input_from_client(
        self,
        from_client: gabriel_pb2.FromClient,
        client_address: str,
        client_info,
    ):
        """Process input received from a client.

        Send it to the targeted engine workers.

        Args:
            from_client: The client input to process.
            client_address: The address of the client.
            client_info: The Any registered by the client, forwarded to
                engine workers alongside the input.
        """
        logger.debug(
            f"Processing input from client {client_address} with source ID "
            f"{from_client.input.producer_id} and frame id "
            f"{from_client.input.frame_id}; target engines: "
            f"{from_client.input.target_engine_ids}"
        )

        CLIENT_INPUTS_RECEIVED_TOTAL.labels(
            producer_id=self._producer_id
        ).inc()

        metadata = _Metadata(
            frame_id=from_client.input.frame_id,
            producer_id=self._producer_id,
            client_address=client_address,
            target_engine_ids=from_client.input.target_engine_ids,
            client_info=client_info,
        )
        payload = from_client.input.input_frame
        metadata_payload = _MetadataPayload(metadata=metadata, payload=payload)

        target_engines = set()
        for engine_worker in self._engine_workers.values():
            if (
                engine_worker.get_engine_id()
                in from_client.input.target_engine_ids
            ):
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
                f"No target engines found for "
                f"{from_client.input.target_engine_ids}; "
                f"{available_engine_ids=}"
            )
            return (
                StatusCode.NO_ENGINE_FOR_INPUT,
                f"No target engines found. Specified target: "
                f"{from_client.input.target_engine_ids}. Available engines: "
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

        # Dispatch to every idle target engine right away, rather than
        # picking just one, so a frame can be processed by more than one
        # engine at once. Only if every target engine is currently busy does
        # it fall back to this producer's queue, to be picked up later via
        # send_next_input.
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
        # engine
        self.latest_input_sent_to_engine = metadata_payload
        self.pending_token_return = True
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
            return None
        metadata_payload = self._input_queue[0]
        self.latest_input_sent_to_engine = metadata_payload
        self.pending_token_return = True
        return self._input_queue.popleft()
