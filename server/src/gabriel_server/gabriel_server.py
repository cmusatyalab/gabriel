import asyncio
import logging
from gabriel_protocol import gabriel_pb2
from gabriel_protocol.gabriel_pb2 import ResultWrapper
from abc import ABC
from abc import abstractmethod
from collections import namedtuple

logger = logging.getLogger(__name__)

class GabrielServer(ABC):
    """
    Connects to Gabriel clients. Consumes input from the clients
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

        # Metadata for each client. 'tokens_for_source' is a dictionary that
        # stores the tokens available for each source. 'task' is an async task
        # that consumes inputs from 'inputs' for each client. 'websocket' is
        # the Websockets handler for this client if using Websockets.
        self._Client = namedtuple('_Client', ['tokens_for_source', 'inputs', 'task', 'websocket'])
        self._num_tokens_per_source = num_tokens_per_source
        # The clients connected to the server
        self._clients = {}
        # The sources consumed by the server
        self._sources_consumed = set()
        # Indicates that the server start up is finished
        self._start_event = asyncio.Event()
        self._is_running = False
        self._engine_cb = engine_cb

    @abstractmethod
    def launch(self, port_or_path, message_max_size, use_ipc=False):
        """
        Launch the Gabriel server synchronously. This method will block execution
        until the server is stopped.

        Args:
            port_or_path: Represents the bind port or the bind unix socket path,
                depending on the value of use_ipc
            message_max_size: The maximum message size accepted over the socket
            use_ipc: Toggles whether the connection is over TCP or IPC
        """
        pass

    @abstractmethod
    async def launch_async(self, port_or_path, message_max_size, use_ipc=False):
        """
        Launch the Gabriel server asynchronously. This method is a coroutine that
        can be awaited, spawned as a task, or cancelled.

        Args:
            port_or_path: Represents the bind port or the bind unix socket path,
                depending on the value of use_ipc
            message_max_size: The maximum message size accepted over the socket
            use_ipc: Toggles whether the connection is over TCP or IPC
        """
        pass

    async def wait_for_start(self):
        """
        Waits for the Gabriel server to start.
        """
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

        return await self._send_via_transport(address, to_client.SerializeToString())

    @abstractmethod
    async def _send_via_transport(self, address, payload):
        """
        Send a payload to the client at the specified address.

        Args:
            address: the identifier of the client to send the payload to
            payload (str): the string payload to send to the client
        """
        pass

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

    @abstractmethod
    def is_running(self):
        """
        Checks whether the Gabriel server is running.
        """
        pass

    @abstractmethod
    async def _handler(self):
        """
        Handles client connections.
        """
        pass

    @abstractmethod
    async def _consumer(self, address):
        """
        Consumes client inputs. Sends an error message to the client on failure.

        Args:
            address: the identifier of the client to consume inputs for
        """
        pass

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

        logger.debug(f"Sending input from client {address} to engine")
        send_success = await self._engine_cb(from_client, address)
        if send_success:
            return ResultWrapper.Status.SUCCESS
        else:
            logger.error('Server dropped frame from: %s', source_name)
            return gabriel_pb2.ResultWrapper.Status.SERVER_DROPPED_FRAME
