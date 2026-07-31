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
        super().__init__(num_tokens_per_producer, engine_cb, engine_ids)
        self._server = None
        # websockets doesn't allow concurrent send()s on the same connection,
        # so use a lock to ensure that we do not interleave sends. The map is
        # keyed on the address of each client.
        self._write_locks: dict[object, asyncio.Lock] = {}

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
        client = self._clients.get(address)
        write_lock = self._write_locks.get(address)
        if client is None or write_lock is None:
            return False

        logger.debug("Sending to address: %s", address)
        try:
            async with write_lock:
                await client.websocket.send(payload)
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

        client = self._new_client(websocket=websocket)
        self._clients[address] = client
        write_lock = asyncio.Lock()
        self._write_locks[address] = write_lock

        try:
            await self._consumer(websocket, client)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            del self._clients[address]
            del self._write_locks[address]
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
                if from_client.WhichOneof("message_type") == "registration":
                    async with self._write_locks[address]:
                        await websocket.send(
                            self._make_registered().SerializeToString()
                        )
                else:
                    # Deduct a token when you get a new input from the client
                    client.tokens_for_producer[
                        from_client.input.producer_id
                    ] -= 1
                continue

            # Send error message
            err_msg = self._make_error_response(
                from_client, status, status_msg
            )

            async with self._write_locks[address]:
                await websocket.send(err_msg.SerializeToString())
