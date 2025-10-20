"""WebSocket Gabriel client used to communicate with a Gabriel server."""

import asyncio
import logging
from typing import Callable
from urllib.parse import urlparse

import websockets
import websockets.client
from gabriel_protocol import gabriel_pb2

from gabriel_client.gabriel_client import (
    GabrielClient,
    InputProducer,
    TokenPool,
)

URI_FORMAT = "ws://{host}:{port}"


logger = logging.getLogger(__name__)
websockets_logger = logging.getLogger(websockets.__name__)

# The entire payload will be printed if this is allowed to be DEBUG
websockets_logger.setLevel(logging.INFO)


class WebsocketClient(GabrielClient):
    """A Gabriel client that talks to the server over WebSockets."""

    def __init__(
        self,
        server_endpoint: str,
        input_producers: list[InputProducer],
        consumer: Callable[[gabriel_pb2.ResultWrapper], None],
    ):
        """Initialize the client.

        Args:
        server_endpoint (str):
            The server endpoint to connect to. Must have the form
            'ws://interface:port'.
        input_producers (List[InputProducer]):
            A list of instances of InputProducer for the inputs
            produced by this client
        consumer (Callable[[gabriel_pb2.ResultWrapper], None]):
            Callback for results from server

        """
        super().__init__()
        self.consumer = consumer
        self.input_producers = set(input_producers)

        self.server_endpoint = server_endpoint
        parsed_server_endpoint = urlparse(server_endpoint.lower())
        if parsed_server_endpoint.scheme != "ws":
            raise ValueError(
                f"Unsupported protocol {parsed_server_endpoint.scheme}"
            )

    def launch(self, message_max_size=None):
        """Launch the client synchronously."""
        asyncio.run(self.launch_async())

    async def launch_async(self):
        """Launch the client asynchronously."""
        async with websockets.connect(
            self.server_endpoint,
            max_size=None,
        ) as websocket:
            self._websocket = websocket
            consumer_task = asyncio.create_task(self._consumer_handler())
            tasks = [
                asyncio.create_task(self._producer_handler(input_producer))
                for input_producer in self.input_producers
            ]
            tasks.append(consumer_task)

            try:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for task in pending:
                task.cancel()
            logger.info("Disconnected From Server")

    async def _consumer_handler(self):
        while self._running:
            try:
                raw_input = await self._websocket.recv()
            except websockets.exceptions.ConnectionClosed:
                return  # stop the handler
            logger.debug("Received input from server")

            to_client = gabriel_pb2.ToClient()
            to_client.ParseFromString(raw_input)

            if to_client.HasField("welcome"):
                self._process_welcome(to_client.welcome)
            elif to_client.HasField("response"):
                self._process_response(to_client.response)
            else:
                raise Exception("Empty to_client message")

    def _process_welcome(self, welcome):
        self._num_tokens_per_source = welcome.num_tokens_per_source
        self._welcome_event.set()

    def _process_response(self, response):
        result_wrapper = response.result_wrapper
        if result_wrapper.status == gabriel_pb2.ResultWrapper.SUCCESS:
            self.consumer(result_wrapper)
        elif (
            result_wrapper.status
            == gabriel_pb2.ResultWrapper.NO_ENGINE_FOR_SOURCE
        ):
            raise Exception("No engine for source")
        else:
            status = result_wrapper.Status.Name(result_wrapper.status)
            logger.error("Output status was: %s", status)

        if response.return_token:
            self._tokens[response.source_id].return_token()

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
        token_pool = TokenPool(self._num_tokens_per_source)
        self._tokens[producer.source_id] = token_pool
        frame_id = 1

        while self._running:
            await token_pool.get_token()

            input_frame = await producer.produce()
            if input_frame is None:
                token_pool.return_token()
                logger.info("Received None from producer")
                continue

            input = gabriel_pb2.ClientInput()
            input.frame_id = frame_id
            frame_id += 1
            input.source_id = producer.source_id
            input.target_engine_ids.extend(producer.get_target_engines())
            input.input_frame.CopyFrom(input_frame)

            from_client = gabriel_pb2.FromClient()
            from_client.input.CopyFrom(input)

            try:
                await self._send_from_client(from_client)
            except websockets.exceptions.ConnectionClosed:
                return  # stop the handler

    async def _send_from_client(self, from_client):
        # Removing this method will break measurement_client
        await self._websocket.send(from_client.SerializeToString())
