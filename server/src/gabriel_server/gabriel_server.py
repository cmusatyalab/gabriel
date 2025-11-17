"""Gabriel server abstract class.

Connects Gabriel cognitive engines to clients.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections import namedtuple
from collections.abc import Callable
from typing import Union

from gabriel_protocol.gabriel_pb2 import (
    FromClient,
    Result,
    StatusCode,
    ToClient,
)

logger = logging.getLogger(__name__)


class GabrielServer(ABC):
    """Connects to Gabriel clients.

    Consumes input from the clients and passes it to the specified callback
    function. Results are sent back to the client as they become available.
    """

    def __init__(
        self,
        num_tokens_per_producer: int,
        engine_cb: Callable[[FromClient], ToClient.ResultWrapper],
    ):
        """Initialize the Gabriel server.

        Args:
            num_tokens_per_producer (int):
                The number of tokens available for each producer
            engine_cb:
                Callback invoked for each input received from a client.
        """
        # Metadata for each client. 'tokens_for_producer' is a dictionary that
        # stores the tokens available for each producer. 'task' is an async
        # task that consumes inputs from 'inputs' for each client. 'websocket'
        # is the Websockets handler for this client if using Websockets.
        self._Client = namedtuple(
            "_Client", ["tokens_for_producer", "inputs", "task", "websocket"]
        )
        self._num_tokens_per_producer = num_tokens_per_producer
        # The clients connected to the server
        self._clients = {}
        # Indicates that the server start up is finished
        self._start_event = asyncio.Event()
        self._is_running = False
        self._engine_cb = engine_cb

    @abstractmethod
    def launch(
        self,
        port_or_path: Union[int, str],
        message_max_size: int,
        use_ipc: bool = False,
    ):
        """Launch the Gabriel server synchronously.

        This method will block execution until the server is stopped.

        Args:
            port_or_path (int | str):
                Represents the bind port or the bind unix socket path,
                depending on the value of use_ipc
            message_max_size (int):
                The maximum message size accepted over the socket in
                bytes
            use_ipc (bool):
                Toggles whether the connection is over TCP or IPC
        """
        pass

    @abstractmethod
    def launch_async(
        self,
        port_or_path: Union[int, str],
        message_max_size: int,
        use_ipc: bool = False,
    ):
        """Launch the Gabriel server asynchronously.

        This method will block execution until the server is stopped.

        Args:
            port_or_path (int | str):
                Represents the bind port or the bind unix socket path,
                depending on the value of use_ipc
            message_max_size (int):
                The maximum message size accepted over the socket in bytes
            use_ipc (bool):
                Toggles whether the connection is over TCP or IPC
        """
        pass

    async def wait_for_start(self):
        """Waits for the Gabriel server to start."""
        await self._start_event.wait()

    async def send_result(
        self,
        address: str,
        producer_id: str,
        frame_id: int,
        engine_id: str,
        result: Result,
        return_token: bool,
    ) -> bool:
        """Send result to client at address.

        Args:
            address (str): The identifier of the client to send the result to
            producer_id (str):
                The id of the producer that the result corresponds to
            frame_id (int):
                The frame id of the input that the result corresponds to
            engine_id (str): The id of the engine that generated the result
            result (Result): The result payload to send to the client
            return_token (bool): Whether to return a token to the client

        Returns True if send succeeded.
        """
        client = self._clients.get(address)
        if client is None:
            logger.warning("Send request to invalid address: %s", address)
            return False

        if producer_id not in client.tokens_for_producer:
            logger.warning(
                "Send request with invalid producer: %s", producer_id
            )
            # Still send so client gets back token
        elif return_token:
            client.tokens_for_producer[producer_id] += 1

        to_client = ToClient()
        to_client.result_wrapper.producer_id = producer_id
        to_client.result_wrapper.return_token = return_token
        to_client.result_wrapper.result.CopyFrom(result)

        return await self._send_via_transport(
            address, to_client.SerializeToString()
        )

    @abstractmethod
    async def _send_via_transport(self, address, payload) -> bool:
        """Send a payload to the client at the specified address.

        Args:
            address: the identifier of the client to send the payload to
            payload (str): the string payload to send to the client
        """
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Checks whether the Gabriel server is running."""
        pass

    @abstractmethod
    async def _client_handler(self):
        """Handles client connections."""
        pass

    @abstractmethod
    async def _consumer(self, address):
        """Consumes client inputs.

        Sends an error message to the client on failure.

        Args:
            address: the identifier of the client to consume inputs for
        """
        pass

    async def _consumer_helper(self, client, address, from_client):
        """Send the input to the engine callback.

        Args:
            client: The client that the input is from
            address: The identifier of the client
            from_client: A FromClient protobuf message containing the input
        """
        producer_id = from_client.producer_id

        if producer_id not in client.tokens_for_producer:
            client.tokens_for_producer[producer_id] = (
                self._num_tokens_per_producer
            )

        if client.tokens_for_producer[producer_id] < 1:
            logger.error(
                f"Client {address} sending from producer {producer_id} "
                f"without tokens"
            )
            return (StatusCode.NO_TOKENS, "No tokens for producer")

        logger.debug(f"Sending input from client {address} to engine")
        send_success, error_msg = await self._engine_cb(from_client, address)
        if send_success:
            return (StatusCode.SUCCESS, "")
        else:
            logger.error(f"Server dropped frame from: {producer_id}")
            return (StatusCode.SERVER_DROPPED_FRAME, error_msg)
