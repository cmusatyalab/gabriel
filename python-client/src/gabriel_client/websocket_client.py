import asyncio
import logging
import socket
from urllib.parse import urlparse
import websockets
import websockets.client

from gabriel_protocol import gabriel_pb2
from collections import namedtuple
from gabriel_client.gabriel_client import GabrielClient
from gabriel_client.gabriel_client import InputProducer
from gabriel_client.gabriel_client import TokenPool

URI_FORMAT = "ws://{host}:{port}"


logger = logging.getLogger(__name__)
websockets_logger = logging.getLogger(websockets.__name__)

# The entire payload will be printed if this is allowed to be DEBUG
websockets_logger.setLevel(logging.INFO)


ProducerWrapper = namedtuple("ProducerWrapper", ["producer", "source_name"])


# It isn't necessary to do this as of Python 3.6 because
# "The socket option TCP_NODELAY is set by default for all TCP connections"
# per https://docs.python.org/3/library/asyncio-eventloop.html
# However, this seems worth keeping in case the default behavior changes.
class NoDelayProtocol(websockets.client.WebSocketClientProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(transport)
        sock = transport.get_extra_info("socket")
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class WebsocketClient(GabrielClient):
    def __init__(self, server_endpoint, input_producers, consumer):
        """
        producer should take no arguments and return an instance of
        gabriel_pb2.InputFrame.
        consumer should take one gabriel_pb2.ResultWrapper and does not need to
        return anything.
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
        event_loop = asyncio.get_event_loop()

        try:
            self._websocket = event_loop.run_until_complete(
                websockets.connect(
                    self.server_endpoint,
                    create_protocol=NoDelayProtocol,
                    max_size=message_max_size,
                )
            )
        except ConnectionRefusedError:
            logger.error("Could not connect to server")
            return

        # We don't waste time checking TCP_NODELAY in production.
        # Note that websocket.transport is an undocumented property.
        # sock = self._websocket.transport.get_extra_info('socket')
        # assert(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1)

        consumer_task = asyncio.ensure_future(self._consumer_handler())
        tasks = [
            asyncio.ensure_future(
                self._producer_handler(
                    producer_wrapper.producer, producer_wrapper.source_name
                )
            )
            for producer_wrapper in self.producer_wrappers
        ]
        tasks.append(consumer_task)

        _, pending = event_loop.run_until_complete(
            asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        )
        for task in pending:
            task.cancel()
        logger.info("Disconnected From Server")

    async def launch_async(self):
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

            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            logger.info("Disconnected From Server")

    async def _consumer_handler(self):
        while self._running:
            try:
                raw_input = await self._websocket.recv()
            except websockets.exceptions.ConnectionClosed:
                return  # stop the handler
            logger.debug("Recieved input from server")

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
        """
        Loop waiting until there is a token available. Then calls producer to
        get the gabriel_pb2.InputFrame to send.
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
            input.target_engine_ids.extend(producer.target_engine_ids)
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
