import asyncio
import logging
import socket
import websockets
import websockets.client

from gabriel_protocol import gabriel_pb2
from gabriel_client.gabriel_client import GabrielClient

URI_FORMAT = 'ws://{host}:{port}'

logger = logging.getLogger(__name__)
websockets_logger = logging.getLogger(websockets.__name__)

# The entire payload will be printed if this is allowed to be DEBUG
websockets_logger.setLevel(logging.INFO)

# It isn't necessary to do this as of Python 3.6 because
# "The socket option TCP_NODELAY is set by default for all TCP connections"
# per https://docs.python.org/3/library/asyncio-eventloop.html
# However, this seems worth keeping in case the default behavior changes.
class NoDelayProtocol(websockets.client.WebSocketClientProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(transport)
        sock = transport.get_extra_info('socket')
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class WebsocketClient(GabrielClient):
    def __init__(self, host, port, producer_wrappers, consumer):
        '''
        producer should take no arguments and return an instance of
        gabriel_pb2.InputFrame.
        consumer should take one gabriel_pb2.ResultWrapper and does not need to
        return anything.
        '''
        super().__init__(host, port, producer_wrappers, consumer, URI_FORMAT)

    def launch(self, message_max_size=None):
        event_loop = asyncio.get_event_loop()

        try:
            self._websocket = event_loop.run_until_complete(
                websockets.client.connect(
                    self._uri, create_protocol=NoDelayProtocol,
                    max_size=message_max_size))
        except ConnectionRefusedError:
            logger.error('Could not connect to server')
            return

        # We don't waste time checking TCP_NODELAY in production.
        # Note that websocket.transport is an undocumented property.
        # sock = self._websocket.transport.get_extra_info('socket')
        # assert(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1)

        consumer_task = asyncio.ensure_future(self._consumer_handler())
        tasks = [
            asyncio.ensure_future(self._producer_handler(
                producer_wrapper.producer,
                producer_wrapper.token_bucket,
                producer_wrapper.target_computation_types))
            for producer_wrapper in self.producer_wrappers
        ]
        tasks.append(consumer_task)

        _, pending = event_loop.run_until_complete(asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED))
        for task in pending:
            task.cancel()
        logger.info('Disconnected From Server')

    async def launch_async(self, message_max_size=None):
        logger.info("Hello from websocket client")
        try:
            self._websocket = await websockets.client.connect(
                    self._uri, create_protocol=NoDelayProtocol,
                    max_size=message_max_size)
        except ConnectionRefusedError:
            logger.error('Could not connect to server')
            return

        # We don't waste time checking TCP_NODELAY in production.
        # Note that websocket.transport is an undocumented property.
        # sock = self._websocket.transport.get_extra_info('socket')
        # assert(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1)

        consumer_task = asyncio.create_task(self._consumer_handler())
        tasks = [
            asyncio.create_task(self._producer_handler(
                producer_wrapper.producer,
                producer_wrapper.token_bucket,
                producer_wrapper.target_computation_types))
            for producer_wrapper in self.producer_wrappers
        ]
        tasks.append(consumer_task)

        try:
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            logger.info("Cancelling tasks")
            for task in tasks:
                if task.done():
                    continue
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            logger.info("Tasks cancelled")
            raise
        logger.info('Disconnected From Server')

    async def _consumer_handler(self):
        while self._running:
            try:
                raw_input = await self._websocket.recv()
            except websockets.exceptions.ConnectionClosed:
                return  # stop the handler

            to_client = gabriel_pb2.ToClient()
            to_client.ParseFromString(raw_input)

            if to_client.HasField('welcome'):
                self._process_welcome(to_client.welcome)
            elif to_client.HasField('response'):
                logger.info(f"{to_client.response.token_bucket}")
                logger.info(f"{to_client.response.computation_type}")
                self._process_response(to_client.response)
            else:
                raise Exception('Empty to_client message')

    async def _producer_handler(self, producer, token_bucket, target_computation_types):
        '''
        Loop waiting until there is a token available. Then calls producer to
        get the gabriel_pb2.InputFrame to send.
        '''

        await self._welcome_event.wait()
        bucket = self._token_buckets.get(token_bucket)
        assert bucket is not None, (f"{token_bucket=} does not exist")

        while self._running:
            await bucket.get_token()

            input_frame = await producer()
            if input_frame is None:
                bucket.return_token()
                logger.info('Received None from producer')
                continue

            from_client = gabriel_pb2.FromClient()
            from_client.frame_id = bucket.get_frame_id()
            from_client.token_bucket = token_bucket
            from_client.target_computation_types[:] = target_computation_types
            from_client.input_frame.CopyFrom(input_frame)

            try:
                await self._send_from_client(from_client)
            except websockets.exceptions.ConnectionClosed:
                return  # stop the handler

            logger.debug('Semaphore for %s is %s', token_bucket,
                         "LOCKED" if bucket.is_locked() else "AVAILABLE")
            bucket.next_frame()

    async def _send_from_client(self, from_client):
        # Removing this method will break measurement_client

        await self._websocket.send(from_client.SerializeToString())
