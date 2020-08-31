import asyncio
import logging
import websockets
from gabriel_protocol import gabriel_pb2
from collections import namedtuple


URI_FORMAT = 'ws://{host}:{port}'


logger = logging.getLogger(__name__)
websockets_logger = logging.getLogger(websockets.__name__)

# The entire payload will be printed if this is allowed to be DEBUG
websockets_logger.setLevel(logging.INFO)


ProducerWrapper = namedtuple('ProducerWrapper', ['producer', 'source_name'])


class WebsocketClient:
    def __init__(self, host, port, producer_wrappers, consumer):
        '''
        producer should take no arguments and return an instance of
        gabriel_pb2.InputFrame.
        consumer should take one gabriel_pb2.ResultWrapper and does not need to
        return anything.
        '''

        self._welcome_event = asyncio.Event()
        self._sources = {}
        self._running = True
        self._uri = URI_FORMAT.format(host=host, port=port)
        self.producer_wrappers = producer_wrappers
        self.consumer = consumer

    def launch(self):
        event_loop = asyncio.get_event_loop()

        try:
            self._websocket = event_loop.run_until_complete(
                websockets.connect(self._uri))
        except ConnectionRefusedError:
            logger.error('Could not connect to server')
            return

        consumer_task = asyncio.ensure_future(self._consumer_handler())
        tasks = [
            asyncio.ensure_future(self._producer_handler(
                producer_wrapper.producer, producer_wrapper.source_name))
            for producer_wrapper in self.producer_wrappers
        ]
        tasks.append(consumer_task)

        _, pending = event_loop.run_until_complete(asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED))
        for task in pending:
            task.cancel()
        logger.info('Disconnected From Server')

    def get_source_names(self):
        return self._sources.keys()

    def stop(self):
        self._running = False
        logger.info('stopping server')

    async def _consumer_handler(self):
        while self._running:
            try:
                raw_input = await self._websocket.recv()
            except websockets.exceptions.ConnectionClosed:
                return  # stop the handler
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

            try:
                await self._send_from_client(from_client)
            except websockets.exceptions.ConnectionClosed:
                return  # stop the handler

            logger.debug('num_tokens for %s is now %d', source_name,
                         source.get_num_tokens())
            source.next_frame()

    async def _send_from_client(self, from_client):
        # Removing this method will break measurement_client

        await self._websocket.send(from_client.SerializeToString())


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
