"""gRPC Gabriel client used to communicate with a Gabriel server."""

import asyncio
import logging
from collections.abc import Iterable
from typing import Callable, Optional

import grpc
from gabriel_protocol import gabriel_pb2, gabriel_pb2_grpc
from gabriel_protocol.tls_utils import build_channel_credentials

from gabriel_client.gabriel_client import (
    DEFAULT_REGISTRATION_RETRY_INTERVAL_SECONDS,
    GabrielClient,
    InputProducer,
    TokenPool,
)

logger = logging.getLogger(__name__)

# Default time to wait before attempting to reconnect after being
# disconnected from the server. Overridden with the reconnect_interval_seconds
# constructor argument.
RECONNECT_INTERVAL_SECONDS = 2

# Default interval between HTTP/2 keepalive pings sent to the server when the
# channel would otherwise be idle, used unless overridden via the
# channel_options constructor argument.
DEFAULT_KEEPALIVE_TIME_MS = 10_000

# Default time to wait for a keepalive ping acknowledgement before the
# connection is considered dead, used unless overridden via the
# channel_options constructor argument.
DEFAULT_KEEPALIVE_TIMEOUT_MS = 5_000

_DEFAULT_CHANNEL_OPTIONS = (
    ("grpc.keepalive_time_ms", DEFAULT_KEEPALIVE_TIME_MS),
    ("grpc.keepalive_timeout_ms", DEFAULT_KEEPALIVE_TIMEOUT_MS),
    ("grpc.keepalive_permit_without_calls", 1),
)


def _merge_channel_options(
    channel_options: Optional[Iterable[tuple]],
) -> tuple:
    """Merge caller-supplied channel options over the defaults.

    Caller-supplied keys take precedence over the default keepalive options.
    """
    merged = dict(_DEFAULT_CHANNEL_OPTIONS)
    if channel_options is not None:
        merged.update(dict(channel_options))
    return tuple(merged.items())


class _DisconnectedError(Exception):
    """Raised internally when the gRPC stream to the server is lost."""


class GrpcClient(GabrielClient):
    """A Gabriel client that talks to the server over gRPC.

    The Gabriel server must be configured to use gRPC for client
    communication.
    """

    def __init__(
        self,
        server_endpoint: str,
        input_producers: Iterable[InputProducer],
        consumer: Callable[[gabriel_pb2.Result], None],
        prometheus_port: int = 8001,
        tls_ca_cert: str = None,
        tls_client_cert: str = None,
        tls_client_key: str = None,
        reconnect_interval_seconds: float = RECONNECT_INTERVAL_SECONDS,
        channel_options: Optional[Iterable[tuple]] = None,
        client_info=None,
        registration_retry_interval_seconds: float = (
            DEFAULT_REGISTRATION_RETRY_INTERVAL_SECONDS
        ),
    ):
        """Initialize the client.

        Args:
            server_endpoint (str):
                The gRPC target to connect to, e.g. 'host:port' for TCP or
                'unix:///path/to/socket' for a Unix domain socket.
            input_producers (Iterable[InputProducer]):
                An iterable of instances of InputProducer for the inputs
                produced by this client
            consumer (Callable[[gabriel_pb2.Result], None]):
                Callback for results from server
            prometheus_port (int):
                Port for Prometheus metrics.
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
            reconnect_interval_seconds (float):
                How long to wait before attempting to reconnect after being
                disconnected from the server.
            channel_options (Iterable[tuple], optional):
                Extra gRPC channel options (e.g. grpc.keepalive_time_ms,
                message size limits) passed to grpc.aio.secure_channel/
                insecure_channel. Overrides the default keepalive options
                (DEFAULT_KEEPALIVE_TIME_MS, DEFAULT_KEEPALIVE_TIMEOUT_MS) for
                any key given.
            client_info (optional):
                Client metadata sent to the server during registration.
            registration_retry_interval_seconds (float):
                How long to wait before retrying registration with the
                server.
        """
        super().__init__(
            prometheus_port,
            client_info=client_info,
            registration_retry_interval_seconds=(
                registration_retry_interval_seconds
            ),
        )
        self._server_endpoint = server_endpoint
        self._credentials = build_channel_credentials(
            tls_ca_cert, tls_client_cert, tls_client_key
        )
        self._reconnect_interval_seconds = reconnect_interval_seconds
        self._channel_options = _merge_channel_options(channel_options)

        self.input_producers = set(input_producers)
        self.consumer = consumer
        # Whether the client is connected to the server
        self._connected = asyncio.Event()
        self._channel = None
        self._call = None

    def remove_input_producer(self, input_producer):
        """Remove an input producer from the client."""
        if input_producer not in self.input_producers:
            return False
        self.input_producers.remove(input_producer)
        return True

    async def launch_async(self):
        """Launch async tasks for running the client.

        Handles producing inputs and consuming results over a bidirectional
        gRPC stream, automatically reconnecting if the stream is
        disconnected.
        """
        while self._running:
            try:
                await self._run_session()
            except _DisconnectedError as e:
                logger.info(
                    f"Disconnected from server; reconnecting in "
                    f"{self._reconnect_interval_seconds}s: {e}"
                )
            if not self._running:
                return
            await asyncio.sleep(self._reconnect_interval_seconds)

    async def _run_session(self):
        """Run a single bidirectional gRPC stream session with the server.

        Raises:
            _DisconnectedError: if the stream is lost while running.

        """
        logger.info(f"Connecting to server at {self._server_endpoint}")
        self._registered_event = asyncio.Event()
        self._connected.clear()
        self._tokens = {}
        self._engine_ids = []
        self._channel = (
            grpc.aio.secure_channel(
                self._server_endpoint,
                self._credentials,
                options=self._channel_options,
            )
            if self._credentials is not None
            else grpc.aio.insecure_channel(
                self._server_endpoint, options=self._channel_options
            )
        )
        stub = gabriel_pb2_grpc.GabrielClientServiceStub(self._channel)
        self._call = stub.ClientSession()

        tasks = [
            asyncio.create_task(self._producer_handler(input_producer))
            for input_producer in self.input_producers
        ]
        tasks.append(asyncio.create_task(self._consumer_handler()))
        tasks.append(
            asyncio.create_task(
                self._registration_handler(self._send_registration)
            )
        )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except _DisconnectedError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception as e:
            logger.error(f"Client encountered exception: {e}")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            await self._channel.close()

    async def _consumer_handler(self):
        """Handle messages from the server."""
        while self._running:
            try:
                to_client = await self._call.read()
            except grpc.aio.AioRpcError as e:
                self._connected.clear()
                raise _DisconnectedError(str(e)) from e

            if to_client is grpc.aio.EOF:
                self._connected.clear()
                raise _DisconnectedError("server closed the session")

            logger.debug("Received message from server")

            if to_client.HasField("registered"):
                logger.info("Registered with server")
                self._process_registered(to_client.registered)
            elif to_client.HasField("result_wrapper"):
                logger.debug("Processing response from server")
                self._process_response(to_client.result_wrapper)
            elif to_client.HasField("engine_ids_update"):
                logger.info("Received engine ids update from server")
                self._engine_ids = to_client.engine_ids_update.engine_ids
                logger.info(f"Updating engine ids to: {self._engine_ids}")
            else:
                logger.critical(
                    "Fatal error: empty to_client message received from server"
                )
                raise Exception("Empty to_client message")

    def _process_registered(self, registered):
        """Process the server's acknowledgement of this client's Registration.

        Args:
            registered:
                The gabriel_pb2.ToClient.Registered message received from
                the server

        """
        self._num_tokens_per_producer = registered.num_tokens_per_producer
        self._engine_ids = registered.engine_ids
        self._connected.set()
        self._registered_event.set()
        logger.info(
            f"Available engines: {self._engine_ids}; "
            f"number of tokens per producer: {self._num_tokens_per_producer}"
        )

    def _process_response(self, result_wrapper):
        """Process a result received from the server.

        Args:
            result_wrapper:
                The gabriel_pb2.ToClient.ResultWrapper message received from
                the server
        """
        result = result_wrapper.result
        result_status = result.status
        code = result_status.code
        msg = result_status.message
        if code == gabriel_pb2.StatusCode.SUCCESS:
            self.record_response_latency(result_wrapper)
            try:
                self.consumer(result)
            except Exception as e:
                logger.error(f"Error processing response from server: {e}")
                raise
        elif code == gabriel_pb2.StatusCode.NO_ENGINE_FOR_INPUT:
            logger.critical(f"Fatal error: no engine for input: {msg}")
            raise Exception(f"No engine for input: {msg}")
        elif code == gabriel_pb2.StatusCode.SERVER_DROPPED_FRAME:
            logger.error(
                f"Engine {result.target_engine_id} dropped frame from "
                f"producer {result_wrapper.producer_id}: {msg}"
            )
        else:
            status_name = gabriel_pb2.StatusCode.Name(code)
            logger.error(
                f"Input from producer {result_wrapper.producer_id} targeting "
                f"engine {result.target_engine_id} caused error "
                f"{status_name}: {msg}"
            )

        if result_wrapper.return_token:
            producer_id = result_wrapper.producer_id
            self._tokens[producer_id].return_token()
            logger.debug(
                f"Returning token for producer {producer_id}, total tokens "
                f"{self._tokens[producer_id].get_remaining_tokens()}"
            )

    async def _producer_handler(self, producer: InputProducer):
        """Generate inputs and sends them to the server.

        Loop waiting until there is a token available. Then call
        producer to get the gabriel_pb2.InputFrame to send.

        Args:
            producer (InputProducer):
                The InputProducer instance that produces inputs for
                this client

        """
        if not await self._wait_while_running(self._registered_event):
            return

        frame_id = 1
        producer_id = producer.producer_id
        token_pool = TokenPool(self._num_tokens_per_producer, producer_id)
        self._tokens[producer_id] = token_pool

        while self._running and producer in self.input_producers:
            if not producer.is_running():
                logger.info(
                    f"Producer {producer.producer_id} is not running; waiting"
                )
                await producer.wait_for_running()
                logger.info(f"Producer {producer.producer_id} resumed")

            await token_pool.get_token()

            input_frame = await producer.produce()
            if input_frame is None:
                token_pool.return_token()
                logger.debug("Received None from producer")
                continue
            if not input_frame.SerializeToString():
                token_pool.return_token()
                logger.error("Input producer produced empty frame")
                continue

            from_client = gabriel_pb2.FromClient()
            from_client.input.frame_id = frame_id
            frame_id += 1
            from_client.input.producer_id = producer.producer_id

            target_engines = set(producer.get_target_engines())
            available_engines = set(self._engine_ids)

            if not target_engines.issubset(available_engines):
                msg = (
                    f"Attempt to target engines that are not connected "
                    f"to the server: {target_engines - available_engines}; "
                    f"{available_engines=}"
                )
                logger.error(msg)
                raise ValueError(msg)

            from_client.input.target_engine_ids.extend(
                producer.get_target_engines()
            )
            from_client.input.input_frame.CopyFrom(input_frame)

            # Send input to server
            logger.debug(
                f"Sending input to server; producer={producer.producer_id}"
            )
            try:
                await self.send_to_server(from_client)
            except (grpc.aio.AioRpcError, asyncio.InvalidStateError) as e:
                raise _DisconnectedError(str(e)) from e

    async def send_to_server(self, from_client: gabriel_pb2.FromClient):
        """Send a frame to the server."""
        self.record_send_metrics(from_client)
        await self._call.write(from_client)

    async def _send_registration(self, from_client: gabriel_pb2.FromClient):
        """Write a Registration message to the server.

        Wraps errors as _DisconnectedError, consistent with other writes to
        the stream, so a lost connection during (re)registration is retried
        rather than treated as fatal.
        """
        try:
            await self._call.write(from_client)
        except (grpc.aio.AioRpcError, asyncio.InvalidStateError) as e:
            raise _DisconnectedError(str(e)) from e
