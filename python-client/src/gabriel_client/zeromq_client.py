import asyncio
import logging
import time
import zmq
import zmq.asyncio

from google.protobuf.message import DecodeError
from gabriel_protocol import gabriel_pb2
from collections import namedtuple
from gabriel_client.source import _Source

URI_FORMAT = 'tcp://{host}:{port}'

logger = logging.getLogger(__name__)

# Represents an input that this client produces. 'producer' is the method
# that produces inputs and 'source_name' is the source that the inputs are for.
ProducerWrapper = namedtuple('ProducerWrapper', ['producer', 'source_name'])

# The duration of time in seconds after which the server is considered to be
# disconnected.
SERVER_TIMEOUT = 10

HEARTBEAT = b''
# The interval in seconds at which a heartbeat is sent to the server
HEARTBEAT_INTERVAL = 1

# Message sent to the server to register this client
HELLO_MSG = b'Hello message'

context = zmq.asyncio.Context()

class ZeroMQClient:
    """
    A Gabriel client that talks to the server over ZeroMQ.
    """
    def __init__(self, host, port, producer_wrappers, consumer):
        """
        Args:
            host (str): the host of the server to connect to
            port (int): the port of the server
            producer_wrappers (list):
                instances of ProducerWrapper for the inputs produced by this
                client
            consumer: callback for results from server
        """
        # Socket used for communicating with the server
        self._socket = context.socket(zmq.DEALER)
        # Whether a welcome message has been received from the server
        self._welcome_event = asyncio.Event()
        # The input sources accepted by the server
        self._sources = {}
        self._running = True
        self._uri = URI_FORMAT.format(host=host, port=port)
        self.producer_wrappers = producer_wrappers
        self.consumer = consumer
        # Whether the client is connected to the server
        self._connected = asyncio.Event()
        # Indicates that a heartbeat was sent to the server but a heartbeat
        # wasn't received back from the server
        self._pending_heartbeat = False
        # The last time a heartbeat was sent to the server
        self._last_heartbeat_time = 0
        # Set by tasks to schedule a heartbeat to be sent to the server
        self._schedule_heartbeat = asyncio.Event()

        self._connected.set()

    def launch(self, message_max_size=None):
        """
        Launch the client.
        """
        asyncio.ensure_future(self._launch_helper())
        asyncio.get_event_loop().run_forever()

    async def _launch_helper(self):
        """
        Launches async tasks for producing inputs, consuming results, and
        sending heartbeats to the server. Also sends a hello message to the
        server to register this client with the server.
        """
        logger.info(f'Connecting to server at {self._uri}')
        self._socket.connect(self._uri)

        await self._socket.send(HELLO_MSG)
        logger.info("Sent hello message to server")

        tasks = [
            self._producer_handler(producer_wrapper.producer, producer_wrapper.source_name)
            for producer_wrapper in self.producer_wrappers
        ]
        tasks.append(self._consumer_handler())
        tasks.append(self._heartbeat_loop())

        await asyncio.gather(*tasks)

    async def _consumer_handler(self):
        """
        Handles messages from the server.
        """
        while self._running:
            # Wait for a message with a timeout, specified in milliseconds
            poll_result = await self._socket.poll(SERVER_TIMEOUT * 1000)
            if (poll_result & zmq.POLLIN) == 0:
                if self._connected.is_set():
                    logger.info("Disconnected from server")
                    self._connected.clear()
                else:
                    # Resend heartbeat in case it was lost
                    logger.debug("Still disconnected; reconnecting and resending heartbeat")
                    self._socket.close(0)
                    self._socket = context.socket(zmq.DEALER)
                    self._socket.connect(self._uri)
                    # Send heartbeat even though we are disconnected
                    await self._send_heartbeat(True)
                continue

            raw_input = await self._socket.recv()

            if not self._connected.is_set():
                logger.info("Reconnected to server")
                self._connected.set()

            if raw_input == HEARTBEAT:
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
        """
        Process a welcome message received from the server.

        Args:
            welcome:
                The gabriel_pb2.ToClient.Welcome message received from the
                server
        """
        for source_name in welcome.sources_consumed:
            self._sources[source_name] = _Source(welcome.num_tokens_per_source)
        self._welcome_event.set()

    def _process_response(self, response):
        """
        Process a response received from the server.

        Args:
            response:
                The gabriel_pb2.ToClient.Response message received from
                the server
        """
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
        """
        Loop waiting until there is a token available. Then calls producer to
        get the gabriel_pb2.InputFrame to send.

        Args:
            producer: The method used to produce inputs for the server
            source_name (str): The name of the source to produce inputs for
        """
        await self._welcome_event.wait()

        source = self._sources.get(source_name)
        assert source is not None, (
            "No engines consume frames from source: {}".format(source_name))

        # Async task used to producer an input
        producer_task = None

        while self._running:
            try:
                # Wait for a token. Time out to send a heartbeat to the server.
                await asyncio.wait_for(source.get_token(), timeout=HEARTBEAT_INTERVAL)
            except TimeoutError:
                self._schedule_heartbeat.set()
                continue
            # Stop producing inputs if disconnected from the server
            if not self._connected.is_set():
                logger.debug("Stopping producer task")
                if producer_task is not None:
                    producer_task.cancel()
                    producer_task = None
                await self._connected.wait()
                logger.debug("Resuming producer task")

            if producer_task is None:
                logger.debug("Created new producer task")
                producer_task = asyncio.create_task(producer())
            try:
                # Wait for the producer to produce an input. Time out to send
                # a heartbeat to the server. Use an asyncio.shield to prevent
                # task cancellation.
                input_frame = await asyncio.wait_for(
                    asyncio.shield(producer_task), timeout=HEARTBEAT_INTERVAL)
            except TimeoutError:
                source.return_token()
                self._schedule_heartbeat.set()
                await asyncio.sleep(0)
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

            # Send input to server
            await self._socket.send(from_client.SerializeToString())

            logger.debug('Semaphore for %s is %s', source_name,
                         "LOCKED" if source.is_locked() else "AVAILABLE")
            source.next_frame()

    async def _send_heartbeat(self, force=False):
        """
        Send a heartbeat to the server.

        Args:
            force:
                Send a heartbeat even if disconnected from the server or if
                sufficient time has not passed since the last heartbeat was
                sent
        """
        # Do not send a heartbeat if disconnected from server or a heartbeat
        # is pending, unless force is True
        if not force and (not self._connected.is_set() or self._pending_heartbeat):
            return
        logger.debug("Sending heartbeat to server")
        self._pending_heartbeat = True
        self._last_heartbeat_time = time.monotonic()
        await self._socket.send(HEARTBEAT)

    async def _heartbeat_loop(self):
        """
        Send a heartbeat when one is scheduled by another task and sufficient
        time has passed since the last heartbeat was sent.
        """
        while self._running:
            if not self._schedule_heartbeat.is_set():
                # Wait for heartbeat to be scheduled by another task
                await self._schedule_heartbeat.wait()
            self._schedule_heartbeat.clear()

            # Heartbeat was sent recently
            if time.monotonic() - self._last_heartbeat_time < HEARTBEAT_INTERVAL:
                continue
            await self._send_heartbeat()
