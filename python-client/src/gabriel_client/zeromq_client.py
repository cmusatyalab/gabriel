"""ZeroMQ Gabriel client used to communicate with a Gabriel server."""

import asyncio
import contextlib
import logging
import time
from typing import Callable, List
from urllib.parse import urlparse

import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2
from google.protobuf.message import DecodeError

from gabriel_client.gabriel_client import (
    GabrielClient,
    InputProducer,
    TokenPool,
)

logger = logging.getLogger(__name__)


# The duration of time in seconds after which the server is considered to be
# disconnected.
SERVER_TIMEOUT_SECS = 10

HEARTBEAT = b""
# The interval in seconds at which a heartbeat is sent to the server
HEARTBEAT_INTERVAL = 1

# Message sent to the server to register this client
HELLO_MSG = b"Hello message"

context = zmq.asyncio.Context()


class ZeroMQClient(GabrielClient):
    """A Gabriel client that talks to the server over ZeroMQ."""

    def __init__(
        self,
        server_endpoint: str,
        input_producers: List[InputProducer],
        consumer: Callable[[gabriel_pb2.ResultWrapper], None],
    ):
        """Initialize the client.

        Args:
        server_endpoint (str):
            The server endpoint to connect to. Must have the form
            'protocol://interface:port'. Protocols supported are
            tcp and ipc
        input_producers (List[InputProducer]):
            A list of instances of InputProducer for the inputs
            produced by this client
        consumer (Callable[[gabriel_pb2.ResultWrapper], None]):
            Callback for results from server

        """
        super().__init__()
        # Socket used for communicating with the server
        self._sock = context.socket(zmq.DEALER)
        # Check whether IPC mode is enabled, and only use the host if so

        parsed_server_endpoint = urlparse(server_endpoint.lower())
        if parsed_server_endpoint.scheme not in ("tcp", "ipc"):
            raise ValueError(
                f"Unsupported protocol {parsed_server_endpoint.scheme}"
            )
        self._server_endpoint = server_endpoint

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

        self._connected.set()

    async def launch_async(self):
        """Launch the client asynchronously."""
        await self._launch_helper()

    def launch(self, message_max_size=None):
        """Launch the client synchronously."""
        asyncio.run(self._launch_helper())

    async def _launch_helper(self):
        """Launch async tasks for running the client.

        Handles producing inputs, consuming results, and sending heartbeats to
        the server. Also sends a hello message to the server to register this
        client with the server.
        """
        logger.info(f"Connecting to server at {self._server_endpoint}")
        self._sock.connect(self._server_endpoint)

        await self._sock.send(HELLO_MSG)
        logger.info("Sent hello message to server")

        tasks = [
            asyncio.create_task(self._producer_handler(input_producer))
            for input_producer in self.input_producers
        ]
        tasks.append(asyncio.create_task(self._heartbeat_loop()))
        tasks.append(asyncio.create_task(self._consumer_handler()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._sock.close(0)
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            # context.destroy()
            raise
        except Exception as e:
            logger.error(f"Client encountered exception: {e}")
            self._sock.close(0)
            for task in tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e2:
                        logger.error(f"Task {task} raised exception: {e2}")
            raise

    async def _consumer_handler(self):
        """Handle messages from the server."""
        while self._running:
            # Wait for a message with a timeout, specified in milliseconds
            poll_result = await self._sock.poll(SERVER_TIMEOUT_SECS * 1000)
            if (poll_result & zmq.POLLIN) == 0:
                if self._connected.is_set():
                    logger.info("Disconnected from server")
                    self._connected.clear()
                else:
                    logger.info(
                        "Still disconnected; reconnecting and resending "
                        "heartbeat"
                    )

                # Resend heartbeat in case it was lost
                self._sock.close(0)
                self._sock = context.socket(zmq.DEALER)
                self._sock.connect(self._server_endpoint)

                # Send heartbeat even though we are disconnected
                await self._send_heartbeat(True)
                continue

            raw_input = await self._sock.recv()

            if not self._connected.is_set():
                logger.info("Reconnected to server")
                self._connected.set()
                # Reset tokens for all sources
                for token_pool in self._tokens.values():
                    token_pool.reset_tokens()

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
        """Process a welcome message received from the server.

        Args:
            welcome:
                The gabriel_pb2.ToClient.Welcome message received from
                the server

        """
        self._num_tokens_per_source = welcome.num_tokens_per_source
        self._welcome_event.set()

    def _process_response(self, response):
        """Process a response received from the server.

        Args:
            response:
                The gabriel_pb2.ToClient.Response message received from
                the server
        """
        result_wrapper = response.result_wrapper
        status = result_wrapper.status
        if status == gabriel_pb2.ResultWrapper.SUCCESS:
            try:
                self.consumer(result_wrapper)
            except Exception as e:
                logger.error(f"Error processing response from server: {e}")
        elif status == gabriel_pb2.ResultWrapper.NO_ENGINE_FOR_SOURCE:
            logger.critical("Fatal error: no engine for source")
            raise Exception("No engine for source")
        elif status == gabriel_pb2.ResultWrapper.SERVER_DROPPED_FRAME:
            logger.error("Server dropped frame")
            raise Exception("Server dropped frame")
        else:
            status_name = result_wrapper.Status.Name(result_wrapper.status)
            logger.error(f"Output status was: {status_name}")

        if response.return_token:
            source_id = response.source_id
            self._tokens[source_id].return_token()
            logger.debug(
                f"Returning token for source {source_id}, total tokens "
                f"{self._tokens[source_id].get_remaining_tokens()}"
            )

    async def _producer_handler(self, producer: InputProducer):
        """Generate inputs and sends them to the server.

        Loop waiting until there is a token available. Then call
        producer to get the gabriel_pb2.InputFrame to send.

        Args:
            producer (InputProducer):
                The InputProducer instance that produces inputs for
                this client

        """
        await self._welcome_event.wait()
        logger.debug("Received welcome from server")

        # Async task used to producer an input
        producer_task = None
        frame_id = 1

        token_pool = TokenPool(self._num_tokens_per_source)
        self._tokens[producer.source_id] = token_pool

        try:
            while self._running:
                logger.debug(f"Producer for {producer.source_id} running")
                if not producer.is_running():
                    logger.info("Producer is not running; waiting")
                    await producer.wait_for_running()

                try:
                    # Wait for a token. Time out to send a heartbeat to the
                    # server.
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
                    # Wait for the producer to produce an input. Time out to
                    # send a heartbeat to the server. Use an asyncio.shield to
                    # prevent task cancellation.
                    input_frame = await asyncio.wait_for(
                        asyncio.shield(producer_task),
                        timeout=HEARTBEAT_INTERVAL,
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
                input.source_id = producer.source_id
                input.target_engine_ids.extend(producer.get_target_engines())
                input.input_frame.CopyFrom(input_frame)

                from_client = gabriel_pb2.FromClient()
                from_client.input.CopyFrom(input)

                # Send input to server
                logger.debug(
                    f"Sending input to server source id {input.source_id}"
                )
                await self._sock.send(from_client.SerializeToString())

                logger.debug(
                    "Semaphore for %s is %s",
                    producer.source_id,
                    "LOCKED" if token_pool.is_locked() else "AVAILABLE",
                )
        except asyncio.CancelledError:
            if producer_task is not None:
                producer_task.cancel()
                asyncio.gather(producer_task, return_exceptions=True)
            raise

    async def _send_heartbeat(self, force=False):
        """Send a heartbeat to the server.

        Args:
            force:
                Send a heartbeat even if disconnected from the server or
                if sufficient time has not passed since the last
                heartbeat was sent

        """
        # Do not send a heartbeat if disconnected from server or a heartbeat
        # is pending, unless force is True
        if not force and (
            not self._connected.is_set() or self._pending_heartbeat
        ):
            return
        logger.debug("Sending heartbeat to server")
        self._pending_heartbeat = True
        self._last_heartbeat_time = time.monotonic()
        await self._sock.send(HEARTBEAT)

    async def _heartbeat_loop(self):
        """Run the client heartbeat loop.

        Send a heartbeat when one is scheduled by another task and
        sufficient time has passed since the last heartbeat was sent.
        """
        while self._running:
            if not self._schedule_heartbeat.is_set():
                # Wait for heartbeat to be scheduled by another task
                await self._schedule_heartbeat.wait()
            self._schedule_heartbeat.clear()

            # Heartbeat was sent recently
            if (
                time.monotonic() - self._last_heartbeat_time
                < HEARTBEAT_INTERVAL
            ):
                continue
            await self._send_heartbeat()
