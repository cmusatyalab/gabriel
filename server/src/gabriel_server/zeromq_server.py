"""Gabriel server that uses ZeroMQ for communication with clients.

Compatible with either TCP or IPC transports.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable

import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2
from google.protobuf.message import DecodeError

from gabriel_server.gabriel_server import GabrielServer

URI_FORMAT = "tcp://*:{}"
IPC_FORMAT = "ipc://{}"

# The duration of time after which a client is considered disconnected
CLIENT_TIMEOUT_SECS = 10

# Message used to check connectivity between the client and the server
HEARTBEAT = b""

# Message sent by the client to register with the server
HELLO_MSG = b"Hello message"

logger = logging.getLogger(__name__)


class ZeroMQServer(GabrielServer):
    """A Gabriel server that uses ZeroMQ for communication with clients."""

    def __init__(
        self,
        num_tokens_per_producer: int,
        engine_cb: Callable[[gabriel_pb2.InputFrame], gabriel_pb2.Result],
    ):
        """Initialize the ZeroMQ server.

        Args:
            num_tokens_per_producer (int):
                Number of tokens allocated to each input producer
            engine_cb:
                A callback function that processes an InputFrame and returns a
                ResultWrapper
        """
        super().__init__(num_tokens_per_producer, engine_cb)
        self._is_running = False
        self._ctx = zmq.asyncio.Context()
        # The socket used for communicating with all clients
        self._sock = self._ctx.socket(zmq.ROUTER)
        # For testing purposes only
        self._simulate_disconnection = False

    def launch(self, port_or_path, message_max_size, use_ipc=False):
        """Launch the ZeroMQ server synchronously.

        This method will block execution until the server is stopped.
        """
        asyncio.run(self.launch_async(port_or_path, message_max_size, use_ipc))

    async def launch_async(
        self, port_or_path, message_max_size, use_ipc=False
    ):
        """Launch the ZeroMQ server asynchronously."""
        self.port_or_path = port_or_path
        self.message_max_size = message_max_size
        self.use_ipc = use_ipc
        logger.info(f"Launching ZeroMQ server on {port_or_path} {use_ipc=}")
        if not use_ipc:
            self._sock.bind(URI_FORMAT.format(port_or_path))
        else:
            self._sock.bind(IPC_FORMAT.format(port_or_path))

        self.handler_task = asyncio.create_task(self._client_handler())
        self._is_running = True

        self._start_event.set()

        logger.info(f"Listening on {port_or_path}")

        try:
            await self.handler_task
        except asyncio.CancelledError:
            self._sock.close(0)
            for client in self._clients.values():
                client.task.cancel()
                await asyncio.gather(client.task, return_exceptions=True)
            self._ctx.destroy()
            raise

    async def _send_via_transport(self, address, payload):
        if self._simulate_disconnection:
            return False
        logger.debug("Sending result to client %s", address)
        await self._sock.send_multipart([address, payload])
        return True

    def is_running(self):
        """Check if the server is running."""
        return self._is_running

    async def _stop_client_handler(self):
        self._simulate_disconnection = True
        await self.handler_task
        self._sock.close(0)

    async def _restart_client_handler(self):
        self._simulate_disconnection = False

        self._sock = self._ctx.socket(zmq.ROUTER)

        if not self.use_ipc:
            self._sock.bind(URI_FORMAT.format(self.port_or_path))
        else:
            self._sock.bind(IPC_FORMAT.format(self.port_or_path))

        self.handler_task = asyncio.create_task(self._client_handler())

        try:
            await self.handler_task
        except asyncio.CancelledError:
            self._sock.close(0)
            for client in self._clients.values():
                client.task.cancel()
                await asyncio.gather(client.task, return_exceptions=True)
            self._ctx.destroy()
            raise

    async def _client_handler(self):
        while self._is_running and not self._simulate_disconnection:
            # Listen for client messages
            try:
                address, raw_input = await self._sock.recv_multipart()
            except (zmq.ZMQError, ValueError) as error:
                logging.error(
                    f"Error '{error.msg}' when receiving on ZeroMQ socket"
                )
                continue

            logger.debug(f"Received message from client {address}")

            client = self._clients.get(address)

            # Register new clients
            if client is None:
                logger.info("New client connected: %s", address)
                task = asyncio.create_task(self._consumer(address))
                task.add_done_callback(handle_task_result)
                client = self._Client(
                    tokens_for_producer={},
                    inputs=asyncio.Queue(),
                    task=task,
                    websocket=None,
                )
                self._clients[address] = client

                # Send client welcome message
                to_client = gabriel_pb2.ToClient()
                to_client.welcome.num_tokens_per_producer = (
                    self._num_tokens_per_producer
                )
                await self._sock.send_multipart(
                    [address, to_client.SerializeToString()]
                )
                logger.debug("Sent welcome message to new client: %s", address)

            if raw_input == HELLO_MSG:
                continue

            # Handle input
            if raw_input == HEARTBEAT:
                logger.debug(f"Received heartbeat from client {address}")
            else:
                logger.debug("Received input from %s", address)
            # Push input to the queue of inputs for this client
            client.inputs.put_nowait(raw_input)

    async def _consumer(self, address):
        try:
            client = self._clients[address]
        except KeyError:
            logger.critical(f"Client {address} not registered")
            raise
        logger.info(f"Consuming inputs for client {address}")

        # Consume inputs for this client as long as it is registered
        while address in self._clients:
            try:
                raw_input = await asyncio.wait_for(
                    client.inputs.get(), CLIENT_TIMEOUT_SECS
                )
            except (TimeoutError, asyncio.TimeoutError):
                logger.info(f"Client disconnected: {address}")
                del self._clients[address]
                return

            # Received heartbeat, send back heartbeat
            if raw_input == HEARTBEAT:
                logger.debug(
                    f"Received heartbeat from client {address}; "
                    f"sending back heartbeat"
                )
                await self._sock.send_multipart([address, HEARTBEAT])
                continue

            from_client = gabriel_pb2.FromClient()
            try:
                from_client.ParseFromString(raw_input)
            except DecodeError as e:
                logger.error(
                    f"Failed to parse input from client {address}: {e}"
                )
                continue

            # Consume input
            status, status_msg = await self._consumer_helper(
                client, address, from_client
            )

            if status == gabriel_pb2.StatusCode.SUCCESS:
                logger.debug(
                    "Consumed input from client %s successfully", address
                )
                client.tokens_for_producer[from_client.producer_id] -= 1
                continue

            # Send error message
            status_name = gabriel_pb2.StatusCode.Name(status)
            logger.error(
                f"Sending error message to client {address}. "
                f"{status_name}: {status_msg}"
            )
            to_client = gabriel_pb2.ToClient()
            to_client.result_wrapper.producer_id = from_client.producer_id
            to_client.result_wrapper.return_token = True
            to_client.result_wrapper.result.status.code = status
            to_client.result_wrapper.result.status.message = status_msg
            to_client.result_wrapper.result.frame_id = from_client.frame_id
            await self._sock.send_multipart(
                [address, to_client.SerializeToString()]
            )


def handle_task_result(t: asyncio.Task):
    """Log exceptions from tasks."""
    with contextlib.suppress(asyncio.CancelledError):
        t.result()
