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
        # that consumes inputs from 'inputs' for each client.  'websocket' is
        # the Websockets handler for this client if using Websockets.
        self._Client = namedtuple('_Client', ['tokens_for_source', 'inputs', 'task', 'websocket'])
        self._num_tokens_per_source = num_tokens_per_source
        # The clients connected to the server
        self._clients = {}
        # The computations consumed by the server
        self._computation_types = set()
        # Indicates that the server start up is finished
        self._start_event = asyncio.Event()
        self._is_running = False
        self._engine_cb = engine_cb

    @abstractmethod
    def launch(self, port, message_max_size):
        pass

    async def wait_for_start(self):
        await self._start_event.wait()

    async def send_result_wrapper(
        self, address, computation_type, source_name, frame_id,
        result_wrapper, return_token):
        """
        Send result to client at address.

        Args:
            address: The identifier of the client to send the result to
            computation_type: The type of computation that the result corresponds to
            source_name: The source of the input that the result corresponds to
            frame_id: The frame id of the input that the result corresponds to
            result_wrapper: The result payload to send to the client
            return_token: Whether to return a token to the client

        Returns True if send succeeded.
        """
        client = self._clients.get(address)
        if client is None:
            logger.warning('Send request to invalid address: %s', address)
            return False

        if return_token:
            client.tokens_for_source[source_name] += 1

        to_client = gabriel_pb2.ToClient()
        to_client.response.eomputation_type = computation_type
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

    def add_computation_type(self, computation_type):
        """
        Indicate that at least one cognitive engine performs computation_type.

        Args:
            computation_type (str): The computation type

        Must be called before self.launch() or run on the same event loop that
        self.launch() uses.
        """
        if computation_type in self._computation_types:
            return

        self._computation_types.add(computation_type)

    def remove_computation_type(self, computation_type):
        """
        Indicate that all cognitive engines that perform computation_type have stopped.

        Args:
            computation_type (str): The computation type

        Must be called before self.launch() or run on the same event loop that
        self.launch() uses.
        """
        if computation_type not in self._computation_types:
            return

        self._computation_types.remove(computation_type)

    @abstractmethod
    def is_running(self):
        pass

    @abstractmethod
    async def _handler(self):
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
        if client.tokens_for_source[source_name] < 1:
            logger.error(
                f'Client {address} sending from source {source_name} without tokens')
            return ResultWrapper.Status.NO_TOKENS

        input_accepted = False
        dropped = True
        for computation_type in from_client.target_computation_types:
            if computation_type not in self._computation_types:
                logger.error(f'No engines perform {computation_type}')
            else:
                input_accepted = True

            logger.debug(f'Sending input from client {address} from source {source_name} for {computation_type}')
            send_success = await self._engine_cb(from_client, address, computation_type)
            if send_success:
                dropped = False
            else:
                logger.error(f'Server dropped frame from client {address} for {computation_type}')

        if not input_accepted:
            return ResultWrapper.Status.NO_ENGINE_FOR_COMPUTATION
        if dropped:
            return gabriel_pb2.ResultWrapper.Status.SERVER_DROPPED_FRAME
        return ResultWrapper.Status.SUCCESS
