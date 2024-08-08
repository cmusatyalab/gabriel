import asyncio
from collections import namedtuple
from gabriel_protocol import gabriel_pb2
import logging
import zmq
import zmq.asyncio

URI_FORMAT = 'tcp://{host}:{port}'

logger = logging.getLogger(__name__)

ProducerWrapper = namedtuple('ProducerWrapper', ['producer', 'source_name'])

# ZeroMQ setup
context = zmq.Context()
socket = context.socket(zmq.DEALER)

class ZeroMQClient:
    def __init__(self, host, port, producer_wrappers, consumer):
        self._welcome_event = asyncio.Event()
        self._sources = {}
        self._running = True
        self._uri = URI_FORMAT.format(host=host, port=port)
        self.producer_wrappers = producer_wrappers
        self.consumer = consumer

    def launch(self, message_max_size=None):
        socket.connect(self._uri)
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_consumer_handler())
            for producer_wrapper in self.producer_wrappers:
                tg.create_task(self._producer_handler(
                    producer_wrapper.producer, producer_wrapper.source_name))

    async def _consumer_handler(self):
        while self._running:
            try:
                raw_input = await socket.recv()
            logger.debug('Recieved input from server')

            to_client = gabriel_pb2.ToClient()
            to_client.ParseFromString(raw_input)

            if to_client.HasField('welcome'):
                self._process_welcome(to_client.welcome)
            elif to_client.HasField('response'):
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
            self.consumer(result_wrapper)
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

        while self._running:
            await source.get_token()

            input_frame = await producer()
            if input_frame is None:
                source.return_token()
                logger.info('Received None from producer')
                continue

            from_client = gabriel_pb2.FromClient()
            from_client.frame_id = source.get_frame_id()
            from_client.source_name = source_name
            from_client.input_frame.CopyFrom(input_frame)

            await socket.send(from_client)

            logger.debug('num_tokens for %s is now %d', source_name,
                         source.get_num_tokens())
            source.next_frame()

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
