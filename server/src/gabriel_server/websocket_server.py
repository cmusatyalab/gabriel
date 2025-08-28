import asyncio
import logging
import socket
from gabriel_protocol import gabriel_pb2
from gabriel_protocol.gabriel_pb2 import ResultWrapper
from gabriel_server.gabriel_server import GabrielServer
import websockets
from websockets.asyncio.server import serve, unix_serve

logger = logging.getLogger(__name__)


class WebsocketServer(GabrielServer):
    def __init__(self, num_tokens_per_source, engine_cb):
        super().__init__(num_tokens_per_source, engine_cb)
        self._server = None

    def launch(self, port_or_path, message_max_size, use_ipc=False):
        asyncio.run(self.launch_async(port_or_path, message_max_size, use_ipc))

    async def launch_async(self, port_or_path, message_max_size, use_ipc=False):
        async with self.get_server(
            self._handler, port_or_path, message_max_size, use_ipc
        ) as server:
            if not use_ipc:
                # Set TCP NO DELAY on all sockets if using TCP
                for sock in server.sockets:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            self._start_event.set()

            logger.info(f"Listening on {port_or_path}")
            await server.serve_forever()

    def get_server(self, handler, port_or_path, max_size, use_ipc):
        if not use_ipc:
            return serve(handler, port=port_or_path, max_size=max_size)
        else:
            return unix_serve(handler, path=port_or_path)

    async def _send_via_transport(self, address, payload):
        logger.debug("Sending to address: %s", address)
        try:
            await self._clients.get(address).websocket.send(payload)
        except websockets.exceptions.ConnectionClosed:
            logger.info("No connection to address: %s", address)
            return False

        return True

    def is_running(self):
        if self._server is None:
            return False

        return self._server.is_serving()

    async def _handler(self, websocket):
        address = websocket.remote_address
        logger.info("New Client connected: %s", address)

        client = self._Client(
            tokens_for_source={
                source_name: self._num_tokens_per_source
                for source_name in self._sources_consumed
            },
            inputs=None,
            task=None,
            websocket=websocket,
        )
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
            logger.info("Client disconnected: %s", address)

    async def _consumer(self, websocket, client):
        address = websocket.remote_address
        async for raw_input in websocket:
            logger.debug("Received input from %s", address)

            from_client = gabriel_pb2.FromClient()
            from_client.ParseFromString(raw_input)

            status = await self._consumer_helper(client, address, from_client)
            if status == ResultWrapper.Status.SUCCESS:
                # Deduct a token when you get a new input from the client
                client.tokens_for_source[from_client.source_name] -= 1
                continue

            # Send error message
            to_client = gabriel_pb2.ToClient()
            to_client.response.source_name = from_client.source_name
            to_client.response.frame_id = from_client.frame_id
            to_client.response.return_token = True
            to_client.response.result_wrapper.status = status
            await websocket.send(to_client.SerializeToString())
