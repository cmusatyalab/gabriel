"""A Gabriel server that uses gRPC for communication with clients."""

import asyncio
import logging

import grpc
from gabriel_protocol import gabriel_pb2, gabriel_pb2_grpc
from gabriel_protocol.tls_utils import build_server_credentials

from gabriel_server.gabriel_server import GabrielServer

logger = logging.getLogger(__name__)


class GrpcServer(GabrielServer, gabriel_pb2_grpc.GabrielClientServiceServicer):
    """A Gabriel server that uses gRPC for communication with clients."""

    def __init__(
        self,
        num_tokens_per_producer,
        engine_cb,
        engine_ids,
        tls_cert=None,
        tls_key=None,
        tls_client_ca_cert=None,
    ):
        """Initialize the gRPC server.

        Args:
            num_tokens_per_producer:
                Number of tokens for flow control for each producer.
            engine_cb:
                Callback invoked when an engine connects or disconnects.
            engine_ids:
                Set of ids of engines expected to connect.
            tls_cert:
                Optional path to a PEM certificate chain for the server to
                present. If either tls_cert or tls_key is omitted, the
                server listens on an insecure (plaintext) port.
            tls_key:
                Optional path to a PEM private key for the server to
                present. If either tls_cert or tls_key is omitted, the
                server listens on an insecure (plaintext) port.
            tls_client_ca_cert:
                Optional path to a PEM CA certificate used to verify client
                certificates. Providing this enables mutual TLS.
        """
        super().__init__(num_tokens_per_producer, engine_cb, engine_ids)
        self._is_running = False
        self._server = None
        self._tls_cert = tls_cert
        self._tls_key = tls_key
        self._tls_client_ca_cert = tls_client_ca_cert
        # gRPC doesn't allow concurrent writes on the same call, so use a lock
        # to ensure that we do not interleave writes. The map is keyed on the
        # gRPC context of the peer.
        self._write_locks: dict[object, asyncio.Lock] = {}

    async def launch_async(
        self, port_or_path, message_max_size, use_ipc=False
    ):
        """Launch the gRPC server asynchronously."""
        self._port_or_path = port_or_path
        self._message_max_size = message_max_size
        self._use_ipc = use_ipc
        await self._start_grpc_server()
        self.mark_started()
        try:
            # Block until this task is cancelled. Awaiting a bare Future here,
            # rather than server.wait_for_termination(), since cancelling this
            # task while it's inside wait_for_termination() can leave the gRPC
            # C-core object in a bad state, and the stop() call in the finally
            # block can end up being a no-op.
            await asyncio.Future()
        finally:
            await self.shutdown()

    async def _start_grpc_server(self):
        """Build, bind, and start the underlying grpc.aio.Server.

        Split out from launch_async so it can also be used to rebind after
        _close_server_socket, for tests simulating a server disconnection.
        """
        options = [
            # Match the gRPC client's keepalive ping interval
            ("grpc.http2.min_ping_interval_without_data_ms", 5_000),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        if self._message_max_size is not None:
            options.append(
                ("grpc.max_send_message_length", self._message_max_size)
            )
            options.append(
                ("grpc.max_receive_message_length", self._message_max_size)
            )
        server = grpc.aio.server(options=options)
        gabriel_pb2_grpc.add_GabrielClientServiceServicer_to_server(
            self, server
        )
        target = (
            f"unix://{self._port_or_path}"
            if self._use_ipc
            else f"[::]:{self._port_or_path}"
        )
        credentials = build_server_credentials(
            self._tls_cert, self._tls_key, self._tls_client_ca_cert
        )
        if credentials is not None:
            server.add_secure_port(target, credentials)
        else:
            server.add_insecure_port(target)
        await server.start()
        self._server = server
        logger.info(f"Listening on {self._port_or_path}")

    def mark_started(self):
        """Signal that the gRPC server has started."""
        self._is_running = True
        self._start_event.set()

    async def shutdown(self):
        """Clean up resources, including shutting down the server."""
        await self._server.stop(grace=None)
        self._is_running = False
        await self.result_manager.cleanup()

    async def _close_server_socket(self):
        """Stop accepting connections, for testing disconnection handling.

        Stopping the grpc.aio.Server immediately tears down every open
        client stream, which is what simulates a server-side disconnection
        for clients; call _recreate_server_socket to bind again afterwards.
        """
        await self._server.stop(grace=None)
        self._clients.clear()
        self._write_locks.clear()

    async def _recreate_server_socket(self):
        """Rebind on the same address after _close_server_socket."""
        await self._start_grpc_server()

    async def _send_via_transport(self, address, payload):
        client = self._clients.get(address)
        write_lock = self._write_locks.get(address)
        if client is None or write_lock is None:
            return False

        to_client = gabriel_pb2.ToClient()
        to_client.ParseFromString(payload)

        logger.debug("Sending result to client %s", address)
        try:
            async with write_lock:
                await client.websocket.write(to_client)
        except (grpc.aio.UsageError, grpc.aio.AioRpcError):
            logger.info("No connection to address: %s", address)
            return False
        except Exception:
            # A write to a call whose peer has already disconnected can surface
            # as a low-level cygrpc error (e.g. ExecuteBatchError) rather than
            # one of grpc.aio's public exception types. Treat any failed write
            # the same way: a best-effort send that failed, not a fatal error
            # for the caller.
            logger.info(
                "Failed to send to address: %s", address, exc_info=True
            )
            return False

        return True

    def is_running(self):
        """Check if the server is running."""
        return self._is_running

    async def ClientSession(self, request_iterator, context):  # noqa: N802
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
        write_lock = asyncio.Lock()
        self._write_locks[address] = write_lock

        # Send welcome message
        async with write_lock:
            await context.write(self._make_welcome())

        try:
            await self._consumer(request_iterator, context, client)
        finally:
            del self._clients[address]
            del self._write_locks[address]
            logger.info(f"Client disconnected: {context.peer()}")

    _client_handler = ClientSession

    async def _consumer(self, request_iterator, context, client):
        address = context
        async for from_client in request_iterator:
            logger.debug(f"Received input from {context.peer()}")

            status, status_msg = await self._consumer_helper(
                client, address, from_client
            )
            if status == gabriel_pb2.StatusCode.SUCCESS:
                client.tokens_for_producer[from_client.input.producer_id] -= 1
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

            async with self._write_locks[address]:
                await context.write(err_msg)
