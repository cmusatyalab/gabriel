import asyncio
import logging
import time
import uuid
import zmq
import zmq.asyncio

from google.protobuf.message import DecodeError
from gabriel_protocol import gabriel_pb2
from collections import namedtuple

URI_FORMAT = "tcp://{host}:{port}"

logger = logging.getLogger(__name__)


class InputProducer:
    def __init__(self, producer, target_engine_ids):
        """
        Args:
            producer (callable): A callable that produces input data
            target_engine_ids (list): A list of target engine IDs for the input
        """
        self._running = asyncio.Event()
        self._running.set()
        self._producer = producer
        self._target_engine_ids = target_engine_ids
        self._source_id = uuid.uuid4()

    async def produce(self):
        """
        Invokes the producer to generate input
        """
        if not self._running:
            raise Exception("Producer called when not running")
        res = await self._producer()
        return res

    def start(self, target_engine_ids):
        """
        Starts the producer

        Args:
            target_engine_ids (list): A list of target engine IDs for the input
        """
        self._target_engine_ids = target_engine_ids
        self._running.set()

    def stop(self):
        """
        Stops the producer
        """
        self._running.clear()

    def is_running(self):
        """
        Checks if the producer is running
        """
        return self._running

    def target_engine_ids(self):
        return self._target_engine_ids

    async def wait_for_running(self):
        """
        Wait until the producer is running
        """
        await self._running.wait()

    def source_id(self):
        """
        Returns the source id of the producer
        """
        return self._source_id


# Represents an input that this client produces. 'producer' is the method
# that produces inputs and 'source_name' is the source that the inputs are for.
ProducerWrapper = namedtuple("ProducerWrapper", ["producer", "source_name"])

# The duration of time in seconds after which the server is considered to be
# disconnected.
SERVER_TIMEOUT_SECS = 10

HEARTBEAT = b""
# The interval in seconds at which a heartbeat is sent to the server
HEARTBEAT_INTERVAL = 1

# Message sent to the server to register this client
HELLO_MSG = b"Hello message"

context = zmq.asyncio.Context()


class TokenPool:
    def __init__(self, num_tokens):
        self._num_tokens = num_tokens
        self._sem = asyncio.Semaphore(num_tokens)

    def return_token(self):
        self._sem.release()

    async def get_token(self):
        logger.debug("Waiting for token")
        await self._sem.acquire()
        logger.debug("Token acquired")

    def is_locked(self):
        return self._sem.locked()


class ZeroMQClient:
    """
    A Gabriel client that talks to the server over ZeroMQ.
    """

    def __init__(self, host, port, input_producers, consumer, use_ipc=False):
        """
        Args:
            host (str): the host of the server to connect to
            port (int): the port of the server
            input_producers (list):
                instances of InputProducer for the inputs produced by this
                client
            consumer: callback for results from server
            ipc (bool): determines whether the ZeroMQ connection should be
                made over Inter-Process Communication (IPC), which will ignore
                the port argument and connect using the host argument only
        """
        # Socket used for communicating with the server
        self._sock = context.socket(zmq.DEALER)
        # Whether a welcome message has been received from the server
        self._welcome_event = asyncio.Event()
        self._producers = {}
        # Mapping from source id to tokens
        self._tokens = dict()
        self._running = True
        # Check whether IPC mode is enabled, and only use the host if so
        if not use_ipc:
            self._uri = URI_FORMAT.format(host=host, port=port)
        else:
            self._uri = host
        self.input_producers = set(input_producers)
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
        self._num_tokens_per_source = None

        self._connected.set()

    async def launch_async(self):
        await self._launch_helper()

    def launch(self, message_max_size=None):
        """
        Launch the client.
        """
        asyncio.run(self._launch_helper())

    def register_input_producer(self, input_producer):
        self.input_producers.add(input_producer)

    def deregister_input_producer(self, input_producer):
        self.input_producers.remove(input_producer)

    async def _launch_helper(self):
        """
        Launches async tasks for producing inputs, consuming results, and
        sending heartbeats to the server. Also sends a hello message to the
        server to register this client with the server.
        """
        logger.info(f"Connecting to server at {self._uri}")
        self._sock.connect(self._uri)

        await self._sock.send(HELLO_MSG)
        logger.info("Sent hello message to server")

        tasks = [
            asyncio.create_task(self._producer_handler(input_producer))
            for input_producer in self.input_producers
        ]
        tasks.append(asyncio.create_task(self._consumer_handler()))
        tasks.append(asyncio.create_task(self._heartbeat_loop()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._sock.close(0)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Destroying ZeroMQ context")
            # context.destroy(0)
            raise

    async def _consumer_handler(self):
        """
        Handles messages from the server.
        """
        while self._running:
            # Wait for a message with a timeout, specified in milliseconds
            poll_result = await self._sock.poll(SERVER_TIMEOUT_SECS * 1000)
            if (poll_result & zmq.POLLIN) == 0:
                if self._connected.is_set():
                    logger.info("Disconnected from server")
                    self._connected.clear()
                else:
                    logger.info(
                        "Still disconnected; reconnecting and resending heartbeat"
                    )

                # Resend heartbeat in case it was lost
                self._sock.close(0)
                self._sock = context.socket(zmq.DEALER)
                self._sock.connect(self._uri)

                # Send heartbeat even though we are disconnected
                await self._send_heartbeat(True)
                continue

            raw_input = await self._sock.recv()

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

            if to_client.HasField("welcome"):
                logger.info("Received welcome from server")
                self._process_welcome(to_client.welcome)
            elif to_client.HasField("response"):
                logger.debug("Processing response from server")
                self._process_response(to_client.response)
            else:
                logger.critical(
                    "Fatal error: empty to_client message received from server"
                )
                raise Exception("Empty to_client message")

    def _process_welcome(self, welcome):
        """
        Process a welcome message received from the server.

        Args:
            welcome:
                The gabriel_pb2.ToClient.Welcome message received from the
                server
        """
        self._num_tokens_per_source = welcome.num_tokens_per_source
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
        if result_wrapper.status == gabriel_pb2.ResultWrapper.SUCCESS:
            try:
                self.consumer(result_wrapper)
            except Exception as e:
                logger.error(f"Error processing response from server: {e}")
        elif result_wrapper.status == gabriel_pb2.ResultWrapper.NO_ENGINE_FOR_SOURCE:
            logger.critical("Fatal error: no engine for source")
            raise Exception("No engine for source")
        else:
            status = result_wrapper.Status.Name(result_wrapper.status)
            logger.error("Output status was: %s", status)

        if response.return_token:
            source_id = uuid.UUID(response.source_id)
            self._tokens[source_id].return_token()

    async def _producer_handler(self, producer):
        """
        Loop waiting until there is a token available. Then calls producer to
        get the gabriel_pb2.InputFrame to send.

        TODO: update
        Args:
            producer: The method used to produce inputs for the server
            source_name (str): The name of the source to produce inputs for
        """
        # logger.debug(f"Producer handler for {producer.source_id()} starting")
        await self._welcome_event.wait()
        logger.debug("Received welcome from server")

        # Async task used to producer an input
        producer_task = None
        frame_id = 1

        token_pool = TokenPool(self._num_tokens_per_source)
        self._tokens[producer.source_id()] = token_pool

        try:
            while self._running:
                logger.debug(f"Producer for {producer.source_id()} running")
                if not producer.is_running():
                    await producer.wait_for_running()

                try:
                    # Wait for a token. Time out to send a heartbeat to the server.
                    await asyncio.wait_for(
                        token_pool.get_token(), timeout=HEARTBEAT_INTERVAL
                    )
                except (TimeoutError, asyncio.TimeoutError):
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
                    producer_task = asyncio.create_task(producer.produce())
                try:
                    # Wait for the producer to produce an input. Time out to send
                    # a heartbeat to the server. Use an asyncio.shield to prevent
                    # task cancellation.
                    input_frame = await asyncio.wait_for(
                        asyncio.shield(producer_task), timeout=HEARTBEAT_INTERVAL
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    token_pool.return_token()
                    self._schedule_heartbeat.set()
                    await asyncio.sleep(0)
                    continue

                logger.debug("Got input from producer")
                producer_task = None

                if input_frame is None:
                    token_pool.return_token()
                    logger.info("Received None from producer")
                    continue

                input = gabriel_pb2.ClientInput()
                input.frame_id = frame_id
                frame_id += 1
                input.source_id = str(producer.source_id())
                input.target_engine_ids.extend(producer.target_engine_ids())
                input.input_frame.CopyFrom(input_frame)

                from_client = gabriel_pb2.FromClient()
                from_client.input.CopyFrom(input)

                # Send input to server
                logger.debug(f"Sending input to server source id {input.source_id}")
                await self._sock.send(from_client.SerializeToString())

                logger.debug(
                    "Semaphore for %s is %s",
                    producer.source_id(),
                    "LOCKED" if token_pool.is_locked() else "AVAILABLE",
                )
        except asyncio.CancelledError:
            if producer_task is not None:
                producer_task.cancel()
                asyncio.gather(producer_task, return_exceptions=True)
            raise

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
        await self._sock.send(HEARTBEAT)

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
