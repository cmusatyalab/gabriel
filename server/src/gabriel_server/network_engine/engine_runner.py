"""Engine runner that connects to the server.

Handles communication between the cognitive engine and the server.
"""

import asyncio
import logging
import threading

import grpc
from gabriel_protocol import gabriel_pb2, gabriel_pb2_grpc
from gabriel_protocol.tls_utils import build_channel_credentials
from google.protobuf.any_pb2 import Any

from gabriel_server import cognitive_engine

TEN_SECONDS = 10
REQUEST_RETRIES = 3

# gRPC keepalive ping interval/timeout for the channel to the server. Detects a
# dead connection without relying on an application-level heartbeat. The
# server's engine-facing grpc.aio.server must permit pings at least this often
# or it will tear down the connection for "too_many_pings".
KEEPALIVE_TIME_MS = 10_000
KEEPALIVE_TIMEOUT_MS = 5_000

# The server also pings this channel itself. This must be at least as
# permissive as that interval, or the channel will tear down the connection for
# "too_many_pings" in the other direction.
KEEPALIVE_MIN_PING_INTERVAL_MS = 5_000

logger = logging.getLogger(__name__)


class _EngineHandlerError(Exception):
    """Raised when the engine's handle() call returns something malformed.

    Carries the ENGINE_ERROR result proto that should still be sent back to the
    server before this propagates up and tears down the session.
    """

    def __init__(self, message, result_proto):
        super().__init__(message)
        self.result_proto = result_proto


class EngineRunner:
    """Connects a cognitive engine to the server.

    Client inputs are sent to the cognitive engine if they specify a target
    engine id that matches the engine id specified in :meth:`__init__`.
    """

    def __init__(
        self,
        engine,
        engine_id: str,
        server_address: str,
        all_responses_required: bool = False,
        timeout: int = TEN_SECONDS,
        request_retries: int = REQUEST_RETRIES,
        tls_ca_cert: str = None,
        tls_client_cert: str = None,
        tls_client_key: str = None,
    ):
        """Initializes the engine runner.

        Args:
            engine:
                The cognitive engine instance to run, must have a handle()
                method.
            engine_id (str): The identifier of the engine.
            server_address (str):
                The gRPC target of the server to connect to, e.g.
                'host:port'.
            all_responses_required (bool):
                Whether all responses are required from the engine.
            timeout (int):
                The timeout in seconds to wait for the channel to the
                server to become ready.
            request_retries (int):
                The number of times to retry connecting to the server.
            tls_ca_cert (str, optional):
                Path to a PEM CA certificate used to verify the server's
                certificate. If omitted (along with tls_client_cert/
                tls_client_key), an insecure (plaintext) channel is used.
            tls_client_cert (str, optional):
                Path to a PEM client certificate presented to the server
                for mutual TLS. Must be given together with
                tls_client_key.
            tls_client_key (str, optional):
                Path to a PEM client private key presented to the server
                for mutual TLS. Must be given together with
                tls_client_cert.
        """
        self.engine = engine
        self.engine_id = engine_id
        self.server_address = server_address
        self.all_responses_required = all_responses_required
        self.timeout = timeout
        self.request_retries = request_retries
        self.credentials = build_channel_credentials(
            tls_ca_cert, tls_client_cert, tls_client_key
        )
        self.stop_event = threading.Event()
        self.done_event = asyncio.Event()

    def run(self):
        """Connects to the server and starts listening to messages."""
        asyncio.run(self.run_async())

    async def run_async(self):
        """Connects to the server and starts listening to messages."""
        retries_left = self.request_retries

        channel_options = [
            ("grpc.keepalive_time_ms", KEEPALIVE_TIME_MS),
            ("grpc.keepalive_timeout_ms", KEEPALIVE_TIMEOUT_MS),
            ("grpc.keepalive_permit_without_calls", 1),
            # Permit the server's own pings toward this channel (see
            # server_runner.py's ENGINE_KEEPALIVE_TIME_MS).
            (
                "grpc.http2.min_ping_interval_without_data_ms",
                KEEPALIVE_MIN_PING_INTERVAL_MS,
            ),
            ("grpc.http2.max_pings_without_data", 0),
        ]

        while not self.stop_event.is_set() and retries_left > 0:
            channel_cm = (
                grpc.aio.secure_channel(
                    self.server_address, self.credentials, channel_options
                )
                if self.credentials is not None
                else grpc.aio.insecure_channel(
                    self.server_address, channel_options
                )
            )
            async with channel_cm as channel:
                try:
                    # The server may not be listening yet (e.g. it is still
                    # starting up). Wait for the channel to become ready rather
                    # than failing immediately on connection refused.
                    await asyncio.wait_for(
                        channel.channel_ready(), timeout=self.timeout
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    logger.warning(
                        f"{self.engine_id}: could not connect to server "
                        f"{self.server_address}"
                    )
                    retries_left -= 1
                    continue

                try:
                    stub = gabriel_pb2_grpc.GabrielEngineServiceStub(channel)
                    call = stub.EngineSession()
                    await self.engine_loop(call)
                    retries_left = self.request_retries
                except grpc.aio.AioRpcError as e:
                    logger.error(
                        f"{self.engine_id}: lost connection to server: {e}"
                    )
                    retries_left -= 1
                except Exception as e:
                    logger.error(e)
                    raise

        self.done_event.set()

        logger.warning(
            f"{self.engine_id} ran out of retries. Abandoning server "
            f"connection."
        )

    async def engine_loop(self, call):
        """Listen for messages from the server."""
        register = gabriel_pb2.FromEngine.Register(
            engine_id=self.engine_id,
            all_responses_required=self.all_responses_required,
        )
        write_lock = asyncio.Lock()
        async with write_lock:
            await call.write(gabriel_pb2.FromEngine(register=register))
        logger.info(
            f"{self.engine_id} sent register message to server "
            f"{self.server_address}"
        )

        frame_queue = asyncio.Queue()

        async def reader():
            while True:
                to_engine = await call.read()

                if to_engine == grpc.aio.EOF:
                    logger.info(f"{self.engine_id}: server closed the session")
                    return

                logger.debug(f"{self.engine_id} received input from server")

                try:
                    frame_queue.put_nowait(
                        (to_engine.input_frame, to_engine.client_info)
                    )
                except asyncio.QueueFull:
                    logger.error(f"{self.engine_id}: queue is full")

        async def stop_watcher():
            # self.stop_event is a threading.Event, set from outside this event
            # loop, so it can only be observed by polling rather than awaiting
            # it directly
            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)

        async def worker():
            while True:
                input_frame, client_info = await frame_queue.get()
                try:
                    # Run the engine handle() in a separate thread, so we do
                    # not block reading from the stream
                    result_proto = await asyncio.to_thread(
                        self._build_result_proto, input_frame, client_info
                    )
                except _EngineHandlerError as e:
                    async with write_lock:
                        await call.write(
                            gabriel_pb2.FromEngine(result=e.result_proto)
                        )
                    raise Exception(str(e)) from e

                logger.debug(f"{self.engine_id} sending result to server")
                async with write_lock:
                    await call.write(
                        gabriel_pb2.FromEngine(result=result_proto)
                    )

        # Reads from the gRPC stream
        reader_task = asyncio.create_task(reader())
        # Invokes handle() on frames received
        worker_task = asyncio.create_task(worker())
        # Checks if we should stop running
        stop_task = asyncio.create_task(stop_watcher())
        tasks = [reader_task, worker_task, stop_task]
        done, pending = set(), tasks
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            if task is stop_task:
                continue
            exc = task.exception()
            if exc is not None:
                raise exc

    def _build_result_proto(self, input_frame, client_info):
        """Run the engine's handle() and build the FromEngine result.

        Raises _EngineHandlerError, carrying an ENGINE_ERROR result proto, if
        the engine returns something malformed.
        """
        result = self.engine.handle(input_frame, client_info)

        result_proto = gabriel_pb2.Result()
        result_proto.target_engine_id = self.engine_id

        if not isinstance(result, cognitive_engine.Result):
            error_msg = (
                f"Incorrect type returned by engine. "
                f"Expected a value of type "
                f"cognitive_engine.Result, found {type(result)}"
            )
            logger.error(error_msg)
            result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
            result_proto.status.message = error_msg
            raise _EngineHandlerError(error_msg, result_proto)

        if not isinstance(result.status, gabriel_pb2.Status):
            error_msg = (
                f"Return status not populated correctly by "
                f"engine. Expected a value of type "
                f"gabriel_pb2.Status, found {type(result.status)}"
            )
            logger.error(error_msg)
            result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
            result_proto.status.message = error_msg
            raise _EngineHandlerError(error_msg, result_proto)

        result_proto.status.CopyFrom(result.status)

        if result.status.code != gabriel_pb2.StatusCode.SUCCESS:
            logger.error(
                f"{self.engine_id} sending error "
                f"{gabriel_pb2.StatusCode.Name(result.status.code)} to "
                f"server"
            )
            return result_proto

        payload = result.payload
        if payload is None:
            error_msg = "Engine did not specify result payload"
            logger.error(error_msg)
            result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
            result_proto.status.message = error_msg
            raise _EngineHandlerError(error_msg, result_proto)

        if isinstance(payload, str):
            result_proto.string_result = payload
        elif isinstance(payload, bytes):
            result_proto.bytes_result = payload
        elif isinstance(payload, Any):
            result_proto.any_result.CopyFrom(payload)
        else:
            error_msg = (
                f"Engine produced unsupported result payload "
                f"type: {type(payload)}"
            )
            logger.error(error_msg)
            result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
            result_proto.status.message = error_msg
            raise _EngineHandlerError(error_msg, result_proto)

        return result_proto

    async def stop(self):
        """Stops the engine runner."""
        self.stop_event.set()
