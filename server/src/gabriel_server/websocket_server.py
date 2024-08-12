import asyncio
import logging
import socket
from gabriel_protocol import gabriel_pb2
from gabriel_protocol.gabriel_pb2 import ResultWrapper
import websockets
import websockets.server
from abc import ABC
from abc import abstractmethod
from collections import namedtuple


logger = logging.getLogger(__name__)
websockets_logger = logging.getLogger(websockets.__name__)

# The entire payload will be logged if this is allowed to be DEBUG
websockets_logger.setLevel(logging.INFO)


_Client = namedtuple('_Client', ['tokens_for_source', 'websocket'])


# It isn't necessary to do this as of Python 3.6 because
# "The socket option TCP_NODELAY is set by default for all TCP connections"
# per https://docs.python.org/3/library/asyncio-eventloop.html
# However, this seems worth keeping in case the default behavior changes.
class NoDelayProtocol(websockets.server.WebSocketServerProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(transport)
        sock = transport.get_extra_info('socket')
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class WebsocketServer(ABC):
    def __init__(self, num_tokens_per_source, engine_cb):
        self._num_tokens_per_source = num_tokens_per_source
        self._clients = {}
        self._sources_consumed = set()
        self._server = None
        self._start_event = asyncio.Event()
        self._engine_cb = engine_cb

    def launch(self, port, message_max_size):
        event_loop = asyncio.get_event_loop()
        start_server = websockets.serve(
            self._handler, port=port, max_size=message_max_size,
            create_protocol=NoDelayProtocol)
        self._server = event_loop.run_until_complete(start_server)

        # It isn't necessary to set TCP_NODELAY on self._server.sockets because
        # these are used for listening and not writing.

        self._start_event.set()
        event_loop.run_forever()

    async def wait_for_start(self):
        await self._start_event.wait()

    async def send_result_wrapper(
            self, address, source_name, frame_id, result_wrapper, return_token):
        '''Send result to client at address.

        Returns True if send succeeded.'''

        client = self._clients.get(address)
        if client is None:
            logger.warning('Send request to invalid address: %s', address)
            return False

        if source_name not in client.tokens_for_source:
            logger.warning('Send request with invalid source: %s', source_name)
            # Still send so client gets back token
        elif return_token:
            client.tokens_for_source[source_name] += 1

        to_client = gabriel_pb2.ToClient()
        to_client.response.source_name = source_name
        to_client.response.frame_id = frame_id
        to_client.response.return_token = return_token
        to_client.response.result_wrapper.CopyFrom(result_wrapper)

        logger.debug('Sending to address: %s', address)
        try:
            await client.websocket.send(to_client.SerializeToString())
        except websockets.exceptions.ConnectionClosed:
            logger.info('No connection to address: %s', address)
            return False

        return True

    def add_source_consumed(self, source_name):
        '''Indicate that at least one cognitive engine consumes frames from
        source_name.

        Must be called before self.launch() or run on the same event loop that
        self.launch() uses.'''

        if source_name in self._sources_consumed:
            return

        self._sources_consumed.add(source_name)
        for client in self._clients.values():
            client.tokens_for_source[source_name] = self._num_tokens_per_source
            # TODO inform client about new source

    def remove_source_consumed(self, source_name):
        '''Indicate that all cognitive engines that consumed frames fome source
        have stopped.

        Must be called before self.launch() or run on the same event loop that
        self.launch() uses.'''

        if source_name not in self._sources_consumed:
            return

        self._sources_consumed.remove(source_name)
        for client in self._clients.values():
            del client.tokens_for_source[source_name]
            # TODO inform client source was removed

    def is_running(self):
        if self._server is None:
            return False

        return self._server.is_serving()

    async def _handler(self, websocket, _):

        # We don't waste time checking TCP_NODELAY in production.
        # Note that websocket.transport is an undocumented property.
        # sock = websocket.transport.get_extra_info('socket')
        # assert(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1)

        address = websocket.remote_address
        logger.info('New Client connected: %s', address)

        client = _Client(
            tokens_for_source={source_name: self._num_tokens_per_source
                               for source_name in self._sources_consumed},
            websocket=websocket)
        self._clients[address] = client

        # Send client welcome message
        to_client = gabriel_pb2.ToClient()
        for source_name in self._sources_consumed:
            to_client.welcome.sources_consumed.append(source_name)
        to_client.welcome.num_tokens_per_source = self._num_tokens_per_source
        await websocket.send(to_client.SerializeToString())

        try:
            await self._consumer(websocket, client)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            del self._clients[address]
            logger.info('Client disconnected: %s', address)

    async def _consumer(self, websocket, client):
        address = websocket.remote_address
        async for raw_input in websocket:
            logger.debug('Received input from %s', address)

            from_client = gabriel_pb2.FromClient()
            from_client.ParseFromString(raw_input)

            status = await self._consumer_helper(client, from_client, address)
            if status == ResultWrapper.Status.SUCCESS:
                client.tokens_for_source[from_client.source_name] -= 1
                continue

            # Send error message
            to_client = gabriel_pb2.ToClient()
            to_client.response.source_name = from_client.source_name
            to_client.response.frame_id = from_client.frame_id
            to_client.response.return_token = True
            to_client.response.result_wrapper.status = status
            await websocket.send(to_client.SerializeToString())

    async def _consumer_helper(self, client, from_client, address):
        source_name = from_client.source_name
        if source_name not in self._sources_consumed:
            logger.error('No engines consume frames from %s', source_name)
            return ResultWrapper.Status.NO_ENGINE_FOR_SOURCE

        if client.tokens_for_source[source_name] < 1:
            logger.error(
                'Client %s sending from source %s without tokens', address,
                source_name)
            return ResultWrapper.Status.NO_TOKENS

        send_success = await self._engine_cb(from_client, address)
        if send_success:
            return ResultWrapper.Status.SUCCESS
        else:
            logger.error('Server dropped frame from: %s', source_name)
            return gabriel_pb2.ResultWrapper.Status.SERVER_DROPPED_FRAME
