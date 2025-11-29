"""A Gabriel server that uses Websockets for communication with clients."""

import asyncio
import logging
import socket

import websockets
from gabriel_protocol import gabriel_pb2
from websockets.asyncio.server import serve, unix_serve

from gabriel_server.gabriel_server import GabrielServer

logger = logging.getLogger(__name__)


class WebsocketServer(GabrielServer):
    """A Gabriel server that uses Websockets for communication with clients."""

    def __init__(self, num_tokens_per_producer, engine_cb, engine_ids):
        """Initialize the Websocket server."""
        super().__init__(num_tokens_per_producer, engine_cb)
        self._server = None
        self._engine_ids = engine_ids

    def launch(self, port_or_path, message_max_size, use_ipc=False):
        """Launch the Websocket server synchronously."""
        asyncio.run(self.launch_async(port_or_path, message_max_size, use_ipc))

    async def launch_async(
        self, port_or_path, message_max_size, use_ipc=False
    ):
        """Launch the Websocket server asynchronously."""
        async with self.get_server(
            self._client_handler, port_or_path, message_max_size, use_ipc
        ) as server:
            self._server = server
            if not use_ipc:
                # Set TCP NO DELAY on all sockets if using TCP
                for sock in server.sockets:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            self._start_event.set()

            logger.info(f"Listening on {port_or_path}")
            await server.serve_forever()

    def get_server(self, handler, port_or_path, max_size, use_ipc):
        """Get the Websocket server."""
        if not use_ipc:
            return serve(handler, "localhost", port_or_path, max_size=max_size)
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
        """Check if the server is running."""
        if self._server is None:
            return False

        return self._server.is_serving()

    async def _client_handler(self, websocket):
        """Handle a new client connection."""
        address = websocket.remote_address
        logger.info("New Client connected: %s", address)

        client = self._Client(
            tokens_for_producer={},
            inputs=asyncio.Queue(),
            task=None,
            websocket=websocket,
        )
        self._clients[address] = client

        # Send client welcome message
        to_client = gabriel_pb2.ToClient()
        to_client.welcome.num_tokens_per_producer = (
            self._num_tokens_per_producer
        )
        to_client.welcome.engine_ids.extend(self._engine_ids)
        await websocket.send(to_client.SerializeToString())

        try:
            await self._consumer(websocket, client)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            del self._clients[address]
            logger.info(f"Client disconnected: {address}")

    async def _consumer(self, websocket, client):
        address = websocket.remote_address
        async for raw_input in websocket:
            logger.debug(f"Received input from {address}")

            from_client = gabriel_pb2.FromClient()
            from_client.ParseFromString(raw_input)

            status, status_msg = await self._consumer_helper(
                client, address, from_client
            )
            if status == gabriel_pb2.StatusCode.SUCCESS:
                # Deduct a token when you get a new input from the client
                client.tokens_for_producer[from_client.producer_id] -= 1
                continue

            # Send error message
            to_client = gabriel_pb2.ToClient()
            to_client.result_wrapper.producer_id = from_client.producer_id
            to_client.result_wrapper.return_token = True
            to_client.result_wrapper.result.frame_id = from_client.frame_id
            to_client.result_wrapper.result.status = status

            await websocket.send(to_client.SerializeToString())

    async def _engines_updated_cb(self):
        to_client = gabriel_pb2.ToClient()
        to_client.control.engine_ids.extend(self._engine_ids)
        msg = to_client.SerializeToString()
        for address in self._clients:
            await self._send_via_transport(address, msg)
