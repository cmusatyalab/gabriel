import asyncio
import logging
import socket
from gabriel_protocol import gabriel_pb2
from gabriel_protocol.gabriel_pb2 import ResultWrapper
from gabriel_server.gabriel_server import GabrielServer
import websockets
import websockets.server

logger = logging.getLogger(__name__)

# It isn't necessary to do this as of Python 3.6 because
# "The socket option TCP_NODELAY is set by default for all TCP connections"
# per https://docs.python.org/3/library/asyncio-eventloop.html
# However, this seems worth keeping in case the default behavior changes.
class NoDelayProtocol(websockets.server.WebSocketServerProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(transport)
        sock = transport.get_extra_info('socket')
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class WebsocketServer(GabrielServer):
    def __init__(self, num_tokens_per_source, engine_cb):
        super().__init__(num_tokens_per_source, engine_cb)
        self._server = None

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

    async def _send_via_transport(self, address, payload):
        logger.debug('Sending to address: %s', address)
        try:
            await self._clients.get(address).websocket.send(payload)
        except websockets.exceptions.ConnectionClosed:
            logger.info('No connection to address: %s', address)
            return False

        return True

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

        client = self._Client(
            tokens_for_source={source_name: self._num_tokens_per_source
                               for source_name in self._sources_consumed},
            inputs=None,
            task=None,
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

            status = await self._consumer_helper(client, address, from_client)
            for s in status:
                if status[s] == ResultWrapper.Status.SUCCESS:
                    logger.debug("Consumed input from %s successfully", address)
                    client.tokens_for_source[s] -= 1
                else:
                    # Send error message
                    logger.error(f"Sending error message to client {address}")
                    to_client = gabriel_pb2.ToClient()
                    to_client.response.source_name = s
                    to_client.response.frame_id = from_client.frame_id
                    to_client.response.return_token = True
                    to_client.response.result_wrapper.status = status[s]
                    await self._sock.send_multipart([
                        address,
                        to_client.SerializeToString()
                    ])

