import asyncio
from collections import namedtuple
from gabriel_protocol import gabriel_pb2
import logging
import zmq
import zmq.asyncio
from google.protobuf.message import DecodeError

URI_FORMAT = 'tcp://{host}:{port}'

logger = logging.getLogger(__name__)

ProducerWrapper = namedtuple('ProducerWrapper', ['producer', 'source_name'])

# ZeroMQ setup
context = zmq.asyncio.Context()
socket = context.socket(zmq.DEALER)

class ZeroMQClient:
    def __init__(self, host, port, producer_wrappers, consumer):
        self._welcome_event = asyncio.Event()
        self._sources = {}
        self._running = True
        self._uri = URI_FORMAT.format(host=host, port=port)
        self.producer_wrappers = producer_wrappers
        self.consumer = consumer
        self._connected = asyncio.Event()
        self._connected.set()
        self._pending_heartbeat = False

    def launch(self, message_max_size=None):
        asyncio.ensure_future(self.launch_helper())
        asyncio.get_event_loop().run_forever()

    async def launch_helper(self):
        logger.info(f'Connecting to server at {self._uri}')
        socket.connect(self._uri)
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._consumer_handler())
            for producer_wrapper in self.producer_wrappers:
                tg.create_task(self._producer_handler(
                    producer_wrapper.producer, producer_wrapper.source_name))
            await socket.send(b'Hello message')
            logger.info("Sent hello message to server")

    async def _consumer_handler(self):
        while self._running:
            try:
                raw_input = await asyncio.wait_for(socket.recv(), 10)
            except TimeoutError:
                if self._connected.is_set():
                    logger.info("Disconected from server")
                    self._connected.clear()
                continue

            if not self._connected.is_set():
                logger.info("Reconnected to server")
                self._connected.set()

            if raw_input == b'':
                logger.debug("Received heartbeat from server")
                self._pending_heartbeat = False
                continue

            to_client = gabriel_pb2.ToClient()
            try:
                to_client.ParseFromString(raw_input)
            except DecodeError as e:
                logger.error(f"Failed to decode message from server: {e}")
                continue

            if to_client.HasField('welcome'):
                logger.info('Received welcome from server')
                self._process_welcome(to_client.welcome)
            elif to_client.HasField('response'):
                logger.debug('Processing response from server')
                self._process_response(to_client.response)
            else:
                raise Exception('Empty to_client message')

    def _process_welcome(self, welcome):
        for source_name in welcome.sources_consumed:
            self._sources[source_name] = _Source(welcome.num_tokens_per_source)
        self._welcome_event.set()

    def _process_response(self, response):
        result_wrapper = response.result_wrapper
        if (result_wrapper.status == gabriel_pb2.ResultWrapper.SUCCESS):
            try:
                self.consumer(result_wrapper)
            except Exception as e:
                logger.error(f"Error processing response from server: {e}")
        elif (result_wrapper.status ==
              gabriel_pb2.ResultWrapper.NO_ENGINE_FOR_SOURCE):
            raise Exception('No engine for source')
        else:
            status = result_wrapper.Status.Name(result_wrapper.status)
            logger.error('Output status was: %s', status)

        if response.return_token:
            self._sources[response.source_name].return_token()

    async def _producer_handler(self, producer, source_name):
        '''
        Loop waiting until there is a token available. Then calls producer to
        get the gabriel_pb2.InputFrame to send.
        '''

        await self._welcome_event.wait()
        source = self._sources.get(source_name)
        assert source is not None, (
            "No engines consume frames from source: {}".format(source_name))
        producer_task = None

        while self._running:
            try:
                await asyncio.wait_for(source.get_token(), timeout=1)
            except TimeoutError:
                asyncio.create_task(self._send_heartbeat())
                continue

            if producer_task is not None and not self._connected.is_set():
                logger.debug("Stopping producer task")
                producer_task.cancel()
                producer_task = None
                await self._connected.wait()
                logger.debug("Resuming producer task")

            if producer_task is None:
                logger.debug("Created new producer task")
                producer_task = asyncio.create_task(producer())
            try:
                input_frame = await asyncio.wait_for(asyncio.shield(producer_task), timeout=1)
            except TimeoutError:
                source.return_token()
                asyncio.create_task(self._send_heartbeat())
                continue

            logger.debug("Got input from producer")
            producer_task = None

            if input_frame is None:
                source.return_token()
                logger.info('Received None from producer')
                continue

            from_client = gabriel_pb2.FromClient()
            from_client.frame_id = source.get_frame_id()
            from_client.source_name = source_name
            from_client.input_frame.CopyFrom(input_frame)

            await socket.send(from_client.SerializeToString())

            logger.debug('num_tokens for %s is now %d', source_name,
                         source.get_num_tokens())
            source.next_frame()

    async def _send_heartbeat(self):
        if not self._connected.is_set() or self._pending_heartbeat:
            return
        logger.debug("Sending heartbeat to server")
        self._pending_heartbeat = True
        await socket.send(b'')

class _Source:
    def __init__(self, num_tokens):
        self._num_tokens = num_tokens
        self._event = asyncio.Event()
        self._frame_id = 0

    def return_token(self):
        self._num_tokens += 1
        self._event.set()

    async def get_token(self):
        while self._num_tokens < 1:
            logger.debug('Waiting for token')
            self._event.clear()  # Clear because we definitely want to wait
            await self._event.wait()

        self._num_tokens -= 1

    def get_num_tokens(self):
        return self._num_tokens

    def get_frame_id(self):
        return self._frame_id

    def next_frame(self):
        self._frame_id += 1
