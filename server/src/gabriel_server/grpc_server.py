"""A Gabriel server that uses gRPC for communication with clients."""

import asyncio
import logging

import grpc
from gabriel_protocol import gabriel_pb2, gabriel_pb2_grpc

from gabriel_server.gabriel_server import GabrielServer

logger = logging.getLogger(__name__)


class GrpcServer(GabrielServer, gabriel_pb2_grpc.GabrielServiceServicer):
    """A Gabriel server that uses gRPC for communication with clients."""

    def __init__(self, num_tokens_per_producer, engine_cb, engine_ids):
        """Initialize the gRPC server."""
        super().__init__(num_tokens_per_producer, engine_cb, engine_ids)
        self._server = None
        self._is_running = False

    async def launch_async(
        self, port_or_path, message_max_size, use_ipc=False
    ):
        """Launch the gRPC server asynchronously."""
        options = []
        if message_max_size is not None:
            options.append(("grpc.max_send_message_length", message_max_size))
            options.append(
                ("grpc.max_receive_message_length", message_max_size)
            )

        self._server = grpc.aio.server(options=options)
        gabriel_pb2_grpc.add_GabrielServiceServicer_to_server(
            self, self._server
        )

        target = (
            f"unix://{port_or_path}" if use_ipc else f"[::]:{port_or_path}"
        )
        self._server.add_insecure_port(target)

        logger.info(f"Launching gRPC server on {port_or_path} {use_ipc=}")
        await self._server.start()
        self._is_running = True
        self._start_event.set()

        logger.info(f"Listening on {port_or_path}")

        try:
            await self._server.wait_for_termination()
        finally:
            self._is_running = False
            await self.result_manager.cleanup()

    async def _send_via_transport(self, address, payload):
        client = self._clients.get(address)
        if client is None:
            return False

        to_client = gabriel_pb2.ToClient()
        to_client.ParseFromString(payload)

        logger.debug("Sending result to client %s", address)
        try:
            await client.websocket.write(to_client)
        except grpc.aio.UsageError:
            logger.info("No connection to address: %s", address)
            return False

        return True

    def is_running(self):
        """Check if the server is running."""
        return self._is_running

    async def Session(self, request_iterator, context):  # noqa: N802
        """Handle a client's stream for its entire lifetime.

        This is invoked directly by the gRPC framework once per client
        connection, and also serves as this transport's `_client_handler`.
        """
        address = context
        logger.info("New client connected: %s", context.peer())

        client = self._Client(
            tokens_for_producer={},
            inputs=asyncio.Queue(),
            task=None,
            websocket=context,
        )
        self._clients[address] = client

        # Send welcome message
        await context.write(self._make_welcome())

        try:
            await self._consumer(request_iterator, context, client)
        finally:
            del self._clients[address]
            logger.info(f"Client disconnected: {context.peer()}")

    _client_handler = Session

    async def _consumer(self, request_iterator, context, client):
        address = context
        async for from_client in request_iterator:
            logger.debug(f"Received input from {context.peer()}")

            status, status_msg = await self._consumer_helper(
                client, address, from_client
            )
            if status == gabriel_pb2.StatusCode.SUCCESS:
                client.tokens_for_producer[from_client.producer_id] -= 1
                continue

            # Send error message
            status_name = gabriel_pb2.StatusCode.Name(status)
            logger.error(
                f"Sending error message to client {context.peer()}. "
                f"{status_name}: {status_msg}"
            )
            err_msg = self._make_error_response(
                from_client, status, status_msg
            )

            await context.write(err_msg)
