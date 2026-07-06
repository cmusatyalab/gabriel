"""gRPC Gabriel client used to communicate with a Gabriel server."""

import asyncio
import logging
from collections.abc import Iterable
from typing import Callable

import grpc
from gabriel_protocol import gabriel_pb2, gabriel_pb2_grpc

from gabriel_client.gabriel_client import (
    GabrielClient,
    InputProducer,
    TokenPool,
)

logger = logging.getLogger(__name__)


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

        """
        super().__init__(prometheus_port)
        self._server_endpoint = server_endpoint

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

        Handles producing inputs and consuming results over a single
        bidirectional gRPC stream opened for the lifetime of the client.
        """
        logger.info(f"Connecting to server at {self._server_endpoint}")
        self._channel = grpc.aio.insecure_channel(self._server_endpoint)
        stub = gabriel_pb2_grpc.GabrielServiceStub(self._channel)
        self._call = stub.Session()

        tasks = [
            asyncio.create_task(self._producer_handler(input_producer))
            for input_producer in self.input_producers
        ]
        tasks.append(asyncio.create_task(self._consumer_handler()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
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
                logger.info(f"Disconnected from server: {e}")
                self._connected.clear()
                return  # stop the handler

            if to_client is grpc.aio.EOF:
                logger.info("Server closed the session")
                self._connected.clear()
                return  # stop the handler

            logger.debug("Received message from server")

            if to_client.HasField("welcome"):
                logger.info("Received welcome from server")
                self._process_welcome(to_client.welcome)
            elif to_client.HasField("result_wrapper"):
                logger.debug("Processing response from server")
                self._process_response(to_client.result_wrapper)
            elif to_client.HasField("control"):
                logger.info("Received control message from server")
                self._engine_ids = to_client.control.engine_ids
                logger.info(f"Updating engine ids to: {self._engine_ids}")
            else:
                logger.critical(
                    "Fatal error: empty to_client message received from server"
                )
                raise Exception("Empty to_client message")

    def _process_welcome(self, welcome):
        """Process a welcome message received from the server.

        Args:
            welcome:
                The gabriel_pb2.ToClient.Welcome message received from
                the server

        """
        self._num_tokens_per_producer = welcome.num_tokens_per_producer
        self._engine_ids = welcome.engine_ids
        self._connected.set()
        self._welcome_event.set()
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
        await self._welcome_event.wait()

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
            from_client.frame_id = frame_id
            frame_id += 1
            from_client.producer_id = producer.producer_id

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

            from_client.target_engine_ids.extend(producer.get_target_engines())
            from_client.input_frame.CopyFrom(input_frame)

            # Send input to server
            logger.debug(
                f"Sending input to server; producer={producer.producer_id}"
            )
            try:
                await self.send_to_server(from_client)
            except (grpc.aio.AioRpcError, asyncio.InvalidStateError) as e:
                logger.info(f"Disconnected from server: {e}")
                return  # stop the handler

    async def send_to_server(self, from_client: gabriel_pb2.FromClient):
        """Send a frame to the server."""
        self.record_send_metrics(from_client)
        await self._call.write(from_client)
