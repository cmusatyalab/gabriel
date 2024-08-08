import asyncio
from collections import namedtuple
from gabriel_protocol import gabriel_pb2
from gabriel_protocol.gabriel_pb2 import ResultWrapper
import logging
import zmq
import zmq.asyncio

URI_FORMAT = 'tcp://*:{}'
CLIENT_TIMEOUT_SECS = 10

logger = logging.getLogger(__name__)

ctx = zmq.asyncio.Context()
socket = ctx.socket(zmq.ROUTER)

_Client = namedtuple('_Client', ['tokens_for_source', 'work_queue'])

class ZeroMQServer:
    def __init__(self, num_tokens_per_source):
        self._num_tokens_per_source = num_tokens_per_source
        self._clients = {}
        self._sources_consumed = set()
        self._start_event = asyncio.Event()
        self._work_queue = asyncio.Queue()
        self._is_running = False

    @abstractmethod
    async def _send_to_engine(self, from_client, address):
        '''Send FromClient to the appropriate engine(s).

        Return True if send succeeded.'''
        pass

    def launch(self, port):
        socket.bind(URI_FORMAT.format(port))
        self._is_running = True
        asyncio.create_task(_handler())
        self._start_event.set()

    async def wait_for_start(self):
        await self._start_event.wait()

    async def send_result_wrapper(
            self, address, source_name, frame_id, result_wrapper, return_token):
        '''Send result to client at address.

        Returns True if send succeeded.'''

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

        logger.debug('Sending to address: %s', address)
        await socket.send_multipart([
            address,
            b'',
            to_client.SerializeToString()
        ])

    def add_source_consumed(self, source_name):
        '''Indicate that at least one cognitive engine consumes frames from
        source_name.

        Must be called before self.launch() or run on the same event loop that
        self.launch() uses.'''

        if source_name in self._sources_consumed:
            return

        self._sources_consumed.add(source_name)
        for client in self._clients.values():
            client.tokens_for_source[source_name] = self._num_tokens_per_source
            # TODO inform client about new source

    def remove_source_consumed(self, source_name):
        '''Indicate that all cognitive engines that consumed frames from source
        have stopped.

        Must be called before self.launch() or run on the same event loop that
        self.launch() uses.'''

        if source_name not in self._sources_consumed:
            return

        self._sources_consumed.remove(source_name)
        for client in self._clients.values():
            del client.tokens_for_source[source_name]
            # TODO inform client source was removed

    def is_running(self):
        return self._is_running

    async def _handler(self):
        while self._is_running:
            # Listen for client messages
            try:
                address, empty, raw_input = await socket.recv_multipart()
            except zmq.ZMQError as error:
                logging.error(f"Error '{error.msg}' when receiving on ZeroMQ socket")
                continue

            client = self._clients.get(address)

            # Register new clients
            if client is None:
                logger.info('New client connected: %s', address)
                client = _Client(
                    tokens_for_source={source_name: self._num_tokens_per_source
                        for source_name in self._sources_consumed},
                    inputs=asyncio.Queue(),
                    task=None)
                self._clients[address] = client
                client.task = asyncio.create_task(_consumer(address))

                # Send client welcome message
                to_client = gabriel_pb2.ToClient()
                for source_name in self._sources_consumed:
                    to_client.welcome.sources_consumed.append(source_name)
                to_client.welcome.num_tokens_per_source = self._num_tokens_per_source
                await socket.send_multipart([
                    address,
                    b'',
                    to_client.SerializeToString()
                ])

            # Handle input
            logger.debug('Received input from %s', address)
            await client.inputs.put(raw_input)

    async def _consumer(self, address):
        client = self._clients.get(address)
        if client is None:
            logging.debug(f"Client {address} not registered")
            return

        while address in self._clients:
            try:
                raw_input = asyncio.wait_for(client.inputs.get(), CLIENT_TIMEOUT_SECS)
            except TimeoutError:
                logger.info(f"Client disconnected: {address}")
                del self._clients[address]
                return

            logger.debug('Consuming input from %s', address)

            from_client = gabriel_pb2.FromClient()
            from_client.ParseFromString(raw_input)

            status = await self._consumer_helper(client, address, from_client)

            if status == ResultWrapper.Status.SUCCESS:
                client.tokens_for_source[from_client.source_name] -= 1
                return

            # Send error message
            to_client = gabriel_pb2.ToClient()
            to_client.response.source_name = from_client.source_name
            to_client.response.frame_id = from_client.frame_id
            to_client.response.return_token = True
            to_client.response.result_wrapper.status = status
            await socket.send_multipart([
                address,
                b'',
                to_client.SerializeToString()
            ])

    async def _consumer_helper(self, client, address, from_client):
        source_name = from_client.source_name
        if source_name not in self._sources_consumed:
            logger.error('No engines consume frames from %s', source_name)
            return ResultWrapper.Status.NO_ENGINE_FOR_SOURCE

        if client.tokens_for_source[source_name] < 1:
            logger.error(
                'Client %s sending from source %s without tokens', address,
                source_name)
            return ResultWrapper.Status.NO_TOKENS

        send_success = await self._send_to_engine(from_client, address)
        if send_success:
            return ResultWrapper.Status.SUCCESS
        else:
            logger.error('Server dropped frame from: %s', source_name)
            return gabriel_pb2.ResultWrapper.Status.SERVER_DROPPED_FRAME

