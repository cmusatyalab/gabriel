import asyncio
from gabriel_protocol import gabriel_pb2
from gabriel_protocol.gabriel_pb2 import ResultWrapper
from gabriel_server import GabrielServer
import logging
import zmq
import zmq.asyncio
from google.protobuf.message import DecodeError

URI_FORMAT = 'tcp://*:{}'

# The duration of time after which a client is considered disconnected
CLIENT_TIMEOUT_SECS = 10

# Message used to check connectivity between the client and the server
HEARTBEAT = b''

# Message sent by the client to register with the server
HELLO_MSG = b'Hello message'

logger = logging.getLogger(__name__)

class ZeroMQServer(GabrielServer):
    def __init__(self, num_tokens_per_source, engine_cb):
        super.__init__(num_tokens_per_source, engine_cb)
        self._is_running = False
        self.ctx = zmq.asyncio.Context()
        # The socket used for communicating with all clients
        self.sock = self.ctx.socket(zmq.ROUTER)

    def launch(self, port, message_max_size):
        asyncio.ensure_future(self._launch_helper(port))
        asyncio.get_event_loop().run_forever()

    async def _launch_helper(self, port):
        """
        Bind to the specified port and launch the client handler.

        Args:
            port (int): the port to bind to
        """
        self.sock.bind(URI_FORMAT.format(port))
        self._is_running = True
        asyncio.create_task(self._handler())
        self._start_event.set()
        logger.info(f"Listening on {URI_FORMAT.format(port)}")

    async def send_via_transport(self,address, payload):
        logger.debug('Sending result to client %s', address)
        await self.sock.send_multipart([
            address,
            payload.SerializeToString()
        ])
        return True

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
                address, raw_input = await self.sock.recv_multipart()
            except (zmq.ZMQError, ValueError) as error:
                logging.error(
                    f"Error '{error.msg}' when receiving on ZeroMQ socket")
                continue


            client = self._clients.get(address)

            # Register new clients
            if client is None:
                logger.info('New client connected: %s', address)
                client = self._Client(
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
                await self.sock.send_multipart([
                    address,
                    to_client.SerializeToString()
                ])
                logger.debug('Sent welcome message to new client: %s', address)

            if raw_input == HELLO_MSG:
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
                self.sock.send_multipart([
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
            await self.sock.send_multipart([
                address,
                to_client.SerializeToString()
            ])
