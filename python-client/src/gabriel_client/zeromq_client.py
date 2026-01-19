"""ZeroMQ Gabriel client used to communicate with a Gabriel server."""

import asyncio
import logging
import time
from collections.abc import Iterable
from typing import Callable
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


class ZeroMQClient(GabrielClient):
    """A Gabriel client that talks to the server over ZeroMQ.

    The Gabriel server must be configured to use ZeroMQ for client
    communication.
    """

    def __init__(
        self,
        server_endpoint: str,
        input_producers: Iterable[InputProducer],
        consumer: Callable[[gabriel_pb2.Result], None],
        prometheus_port: int = 8001,
    ):
        """Initialize the client.

        Args:
        server_endpoint (str):
            The server endpoint to connect to. Must have the form
            'protocol://interface:port'. Protocols supported are
            tcp and ipc
        input_producers (Iterable[InputProducer]):
            An iterable of instances of InputProducer for the inputs
            produced by this client
        consumer (Callable[[gabriel_pb2.Result], None]):
            Callback for results from server
        prometheus_port (int):
            Port for Prometheus metrics.

        """
        super().__init__(prometheus_port)
        # Socket used for communicating with the server
        self._ctx = zmq.asyncio.Context()
        self._sock = self._ctx.socket(zmq.DEALER)
        self._sock.setsockopt(zmq.LINGER, 0)
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

    def remove_input_producer(self, input_producer):
        """Remove an input producer from the client."""
        if input_producer not in self.input_producers:
            return False
        self.input_producers.remove(input_producer)
        return True

    async def launch_async(self):
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
        tasks.append(asyncio.create_task(self._consumer_handler()))
        tasks.append(asyncio.create_task(self._heartbeat_loop()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception as e:
            logger.error(f"Client encountered exception: {e}")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            self._sock.close()
            self._ctx.term()

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
                self._sock.close()
                self._sock = self._ctx.socket(zmq.DEALER)
                self._sock.setsockopt(zmq.LINGER, 0)
                self._sock.connect(self._server_endpoint)

                # Send heartbeat even though we are disconnected
                await self._send_heartbeat(True)
                continue

            raw_input = await self._sock.recv()

            if not self._connected.is_set():
                logger.info("Reconnected to server")
                self._connected.set()
                # Reset tokens for all producers
                for token_pool in self._tokens.values():
                    token_pool.reset_tokens()

            if raw_input == HEARTBEAT:
                logger.debug("Received heartbeat from server")
                self._pending_heartbeat = False
                continue

            logger.debug("Received message from server")

            to_client = gabriel_pb2.ToClient()
            try:
                to_client.ParseFromString(raw_input)
            except DecodeError as e:
                logger.error(f"Failed to decode message from server: {e}")
                continue

            if to_client.HasField("welcome"):
                logger.info("Received welcome from server")
                self._process_welcome(to_client.welcome)
            elif to_client.HasField("result_wrapper"):
                logger.debug("Processing response from server")
                self._process_response(to_client.result_wrapper)
            elif to_client.HasField("control"):
                logger.info("Received control message from server")
                self._engine_ids = to_client.control.engine_ids
                logger.info(f"Updating engine ids to: {self._engine_ids}")
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
        self._num_tokens_per_producer = welcome.num_tokens_per_producer
        self._engine_ids = welcome.engine_ids
        self._welcome_event.set()
        logger.info(
            f"Available engines: {self._engine_ids}; "
            f"number of tokens per producer: {self._num_tokens_per_producer}"
        )

    def _process_response(self, result_wrapper):
        """Process a result received from the server.

        Args:
            result_wrapper:
                The gabriel_pb2.ToClient.ResultWrapper message received from
                the server
        """
        result = result_wrapper.result
        result_status = result.status
        code = result_status.code
        msg = result_status.message
        if code == gabriel_pb2.StatusCode.SUCCESS:
            self.record_response_latency(result_wrapper)
            try:
                self.consumer(result)
            except Exception as e:
                logger.error(f"Error processing response from server: {e}")
                raise
        elif code == gabriel_pb2.StatusCode.NO_ENGINE_FOR_INPUT:
            logger.critical(f"Fatal error: no engine for input: {msg}")
            raise Exception(f"No engine for input: {msg}")
        elif code == gabriel_pb2.StatusCode.SERVER_DROPPED_FRAME:
            logger.error(
                f"Engine {result.target_engine_id} dropped frame from "
                f"producer {result_wrapper.producer_id}: {msg}"
            )
        else:
            status_name = gabriel_pb2.StatusCode.Name(code)
            logger.error(
                f"Input from producer {result_wrapper.producer_id} targeting "
                f"engine {result.target_engine_id} caused error "
                f"{status_name}: {msg}"
            )

        if result_wrapper.return_token:
            producer_id = result_wrapper.producer_id
            self._tokens[producer_id].return_token()
            logger.debug(
                f"Returning token for producer {producer_id}, total tokens "
                f"{self._tokens[producer_id].get_remaining_tokens()}"
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

        # Async task used to producer an input
        producer_task = None
        frame_id = 1

        producer_id = producer.producer_id
        token_pool = TokenPool(self._num_tokens_per_producer, producer_id)
        self._tokens[producer_id] = token_pool

        try:
            while self._running and producer in self.input_producers:
                if not producer.is_running():
                    logger.info(
                        f"Producer {producer.producer_id} is not running; "
                        f"waiting"
                    )
                    await producer.wait_for_running()
                    logger.info(f"Producer {producer.producer_id} resumed")

                try:
                    # Wait for a token. Time out to send a heartbeat to the
                    # server.
                    await asyncio.wait_for(
                        token_pool.get_token(), timeout=HEARTBEAT_INTERVAL
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    self._schedule_heartbeat.set()
                    continue

                target_engines = producer.get_target_engines()
                if not target_engines:
                    logger.error(
                        f"{producer.producer_name} targets no engines"
                    )

                    token_pool.return_token()
                    raise ValueError(
                        f"{producer.producer_name} targets no engines"
                    )
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
                    logger.debug("Received None from producer")
                    continue
                if not input_frame.SerializeToString():
                    token_pool.return_token()
                    logger.error("Input producer produced empty frame")
                    continue

                from_client = gabriel_pb2.FromClient()
                from_client.frame_id = frame_id
                frame_id += 1
                from_client.producer_id = producer.producer_id

                target_engines = set(producer.get_target_engines())
                available_engines = set(self._engine_ids)

                if not target_engines.issubset(available_engines):
                    msg = (
                        f"Attempt to target engines that are not connected "
                        f"to the server: {target_engines - available_engines}"
                    )
                    logger.error(msg)
                    raise ValueError(msg)

                from_client.target_engine_ids.extend(
                    producer.get_target_engines()
                )
                from_client.input_frame.CopyFrom(input_frame)

                # Send input to server
                logger.debug(
                    f"Sending input to server; producer={producer.producer_id}"
                )
                await self.send_to_server(from_client)

                logger.debug(
                    "Semaphore for %s is %s",
                    producer.producer_id,
                    "LOCKED" if token_pool.is_locked() else "AVAILABLE",
                )
        finally:
            if producer_task is not None:
                producer_task.cancel()
                asyncio.gather(producer_task, return_exceptions=True)

    async def send_to_server(self, from_client: gabriel_pb2.FromClient):
        """Send a frame to the server."""
        self.record_send_metrics(from_client)
        await self._sock.send(from_client.SerializeToString())

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
