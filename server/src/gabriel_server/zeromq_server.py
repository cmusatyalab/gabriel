import asyncio
from collections import namedtuple
from gabriel_protocol import gabriel_pb2
from gabriel_protocol.gabriel_pb2 import ResultWrapper
import logging
import zmq
import zmq.asyncio
from abc import abstractmethod
from abc import ABC
import pdb

URI_FORMAT = 'tcp://*:{}'

# The duration of time after which a client is considered disconnected
CLIENT_TIMEOUT_SECS = 10

# Message used to check connectivity between the client and the server
HEARTBEAT = b''

logger = logging.getLogger(__name__)

ctx = zmq.asyncio.Context()
# The socket used for communicating with all clients
socket = ctx.socket(zmq.ROUTER)

# Metadata for each client. 'tokens_for_source' is a dictionary that stores
# the tokens available for each source. 'task' is an async task that consumes
# inputs from 'inputs' for each client.
_Client = namedtuple('_Client', ['tokens_for_source', 'inputs', 'task'])

class ZeroMQServer(ABC):
    """
    Connects to Gabriel clients using ZeroMQ. Consumes input from the clients
    and passes it to the specified callback function. Results are sent back to
    the client as they become available.
    """
    def __init__(self, num_tokens_per_source, engine_cb):
        """
        Args:
            num_tokens_per_source (int):
                The number of tokens available for each source
            engine_cb:
                Callback invoked for each input received from a client
        """
        self._num_tokens_per_source = num_tokens_per_source
        self._clients = {}
        self._sources_consumed = set()
        self._start_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._is_running = False
        self._engine_cb = engine_cb

    def launch(self, port, message_max_size):
        """
        Launch the server.

        Args:
            port (int): the port to bind to
        """
        asyncio.ensure_future(self._launch_helper(port))
        asyncio.get_event_loop().run_forever()

    async def _launch_helper(self, port):
        """
        Bind to the specified port and launch the client handler.

        Args:
            port (int): the port to bind to
        """
        socket.bind(URI_FORMAT.format(port))
        self._is_running = True
        asyncio.create_task(self._handler())
        self._start_event.set()
        logger.info(f"Listening on {URI_FORMAT.format(port)}")

    async def wait_for_start(self):
        await self._start_event.wait()

    async def send_result_wrapper(
            self, address, source_name, frame_id, result_wrapper, return_token):
        """
        Send result to client at address.

        Args:
            address: The identifier of the client to send the result to
            source_name: The name of the source that the result corresponds to
            frame_id: The frame id of the input that the result corresponds to
            result_wrapper: The result payload to send to the client
            return_token: Whether to return a token to the client

        Returns True if send succeeded.
        """
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

        logger.debug('Sending result to client %s', address)
        await socket.send_multipart([
            address,
            to_client.SerializeToString()
        ])

    def add_source_consumed(self, source_name):
        """
        Indicate that at least one cognitive engine consumes frames from
        source_name.

        Args:
            source_name (str): The name of the source to add

        Must be called before self.launch() or run on the same event loop that
        self.launch() uses.
        """

        if source_name in self._sources_consumed:
            return

        self._sources_consumed.add(source_name)
        for client in self._clients.values():
            client.tokens_for_source[source_name] = self._num_tokens_per_source
            # TODO inform client about new source

    def remove_source_consumed(self, source_name):
        """
        Indicate that all cognitive engines that consumed frames from source
        have stopped.

        Args:
            source_name (str): The name of the source to remove

        Must be called before self.launch() or run on the same event loop that
        self.launch() uses.
        """
        if source_name not in self._sources_consumed:
            return

        self._sources_consumed.remove(source_name)
        for client in self._clients.values():
            del client.tokens_for_source[source_name]
            # TODO inform client source was removed

    def is_running(self):
        """Returns true if the server is still running."""
        return self._is_running

    async def _handler(self):
        """
        Handles incoming client messages.

        When a new client is connected it is registered and a welcome message
        is sent to the client, indicating that the server is ready to start
        receiving inputs from the client. For each client, keeps track of the
        number of tokens available for each source.
        """
        while self._is_running:
            # Listen for client messages
            try:
                address, raw_input = await socket.recv_multipart()
            except zmq.ZMQError as error:
                logging.error(
                    f"Error '{error.msg}' when receiving on ZeroMQ socket")
                continue

            client = self._clients.get(address)

            # Register new clients
            if client is None:
                logger.info('New client connected: %s', address)
                client = _Client(
                    tokens_for_source={source_name: self._num_tokens_per_source
                        for source_name in self._sources_consumed},
                    inputs=asyncio.Queue(),
                    task=asyncio.create_task(self._consumer(address)))
                self._clients[address] = client

                # Send client welcome message
                to_client = gabriel_pb2.ToClient()
                for source_name in self._sources_consumed:
                    to_client.welcome.sources_consumed.append(source_name)
                to_client.welcome.num_tokens_per_source = self._num_tokens_per_source
                await socket.send_multipart([
                    address,
                    to_client.SerializeToString()
                ])
                logger.debug('Sent welcome message to new client: %s', address)

            if raw_input == b'Hello message':
                continue

            # Handle input
            if raw_input == HEARTBEAT:
                logger.debug(f'Received heartbeat from client {address}')
            else:
                logger.debug('Received input from %s', address)
            # Push input to the queue of inputs for this client
            client.inputs.put_nowait(raw_input)

    async def _consumer(self, address):
        """
        Consumes client inputs. Sends an error message to the client on failure.

        Args:
            address: the identifier of the client to consume inputs for
        """
        client = self._clients.get(address)
        if client is None:
            logging.debug(f"Client {address} not registered")
            return
        logging.debug(f"Consuming inputs for client {address}")

        # Consume inputs for this client as long as it is registered
        while address in self._clients:
            try:
                raw_input = await asyncio.wait_for(client.inputs.get(), CLIENT_TIMEOUT_SECS)
            except TimeoutError:
                logger.info(f"Client disconnected: {address}")
                del self._clients[address]
                return

            # Received heartbeat, send back heartbeat
            if raw_input == HEARTBEAT:
                socket.send_multipart([
                    address,
                    HEARTBEAT
                ])
                continue

            from_client = gabriel_pb2.FromClient()
            try:
                from_client.ParseFromString(raw_input)
            except DecodeError as e:
                logger.error(f"Failed to parse input from client {address}: {e}")
                continue

            # Consume input
            status = await self._consumer_helper(client, address, from_client)

            if status == ResultWrapper.Status.SUCCESS:
                logging.debug("Consumed input from %s successfully", address)
                client.tokens_for_source[from_client.source_name] -= 1
                continue

            # Send error message
            logging.debug(f"Sending error message to client {address}")
            to_client = gabriel_pb2.ToClient()
            to_client.response.source_name = from_client.source_name
            to_client.response.frame_id = from_client.frame_id
            to_client.response.return_token = True
            to_client.response.result_wrapper.status = status
            await socket.send_multipart([
                address,
                to_client.SerializeToString()
            ])

    async def _consumer_helper(self, client, address, from_client):
        """
        Send the input to the engine callback.

        Args:
            client: The client that the input is from
            address: The identifier of the client
            from_client: A FromClient protobuf message containing the input
        """
        source_name = from_client.source_name
        if source_name not in self._sources_consumed:
            logger.error('No engines consume frames from %s', source_name)
            return ResultWrapper.Status.NO_ENGINE_FOR_SOURCE

        if client.tokens_for_source[source_name] < 1:
            logger.error(
                'Client %s sending from source %s without tokens', address,
                source_name)
            return ResultWrapper.Status.NO_TOKENS

        logging.debug(f"Sending input from client {address} to engine")
        send_success = await self._engine_cb(from_client, address)
        if send_success:
            return ResultWrapper.Status.SUCCESS
        else:
            logger.error('Server dropped frame from: %s', source_name)
            return gabriel_pb2.ResultWrapper.Status.SERVER_DROPPED_FRAME

