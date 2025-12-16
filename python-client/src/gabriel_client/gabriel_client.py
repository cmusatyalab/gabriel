"""Abstract base class for a Gabriel client and related classes."""

import asyncio
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Iterable
from typing import Any, Union

from gabriel_protocol.gabriel_pb2 import FromClient, InputFrame, ToClient
from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

PRODUCER_TOKEN_COUNT = Gauge(
    "gabriel_producer_token_count",
    "Number of tokens remaining at each producer",
    ["producer_id"],
)

CLIENT_INPUTS_SENT_TOTAL = Counter(
    "gabriel_producer_inputs_sent_total",
    "Total number of client inputs sent from a producer",
    ["producer_id"],
)

CLIENT_INPUT_PROCESSING_LATENCY = Histogram(
    "gabriel_client_input_processing_latency_seconds",
    "End-to-end client input processing latency",
    ["producer_id"],
)


class InputProducer:
    """An input producer that produces inputs to send to the server.

    Stores a callback for producing input frames as well as the identifiers
    of the cognitive engines to target.

    The input producer can be stopped and resumed using :meth:`stop` and
    :meth:`resume`. The current status (stopped/running) can be obtained using
    :meth:`is_running`. By default, the input producer is running.

    The methods of this class are thread-safe.
    """

    def __init__(
        self,
        producer: Callable[[], Coroutine[Any, Any, InputFrame | None]],
        target_engine_ids: Iterable[str],
        producer_name: Union[str, None] = None,
    ):
        """Initialize the input producer.

        Args:
            producer (coroutine function):
                A coroutine function that produces input data
            target_engine_ids (Iterable[str]):
                Target engine IDs for the input
            producer_name (str, optional):
                The name of the producer producing the input
        """
        self._running = threading.Event()
        self._running.set()
        self._producer = producer
        self._target_engine_ids = set(target_engine_ids)
        self._target_engine_lock = threading.Lock()
        self.producer_name = producer_name
        self.producer_id = (
            producer_name + "-" + str(uuid.uuid4())
            if producer_name
            else str(uuid.uuid4())
        )
        self._loop = None

    async def produce(self) -> InputFrame | None:
        """Invoke the producer to generate input.

        Raises:
            Exception: if the producer is not running
        """
        if not self._running.is_set():
            raise Exception(
                f"Producer {self.producer_name} called when not running"
            )
        res = await self._producer()
        return res

    def resume(self) -> None:
        """Resume the producer."""
        if self._running.is_set():
            raise Exception("Producer already started")
        self._running.set()
        with self._target_engine_lock:
            logger.info(
                f"Resuming producer {self.producer_name} and targeting "
                f"engines {self._target_engine_ids}"
            )

    def stop(self) -> None:
        """Stop the producer."""
        logger.info("Stopping producer")
        self._running.clear()

    def change_target_engines(self, target_engine_ids: Iterable[str]) -> None:
        """Change the target engines for the producer.

        Args:
            target_engine_ids (list[str]):
                A list of target engine IDs for the input

        """
        with self._target_engine_lock:
            self._target_engine_ids = set(target_engine_ids)
        logger.info(
            f"Changing target engines to {target_engine_ids} for "
            f"producer {self.producer_name}"
        )

    def add_target_engine(self, target_engine_id: str) -> None:
        """Add a target engine to producer.

        Args:
            target_engine_id (str): a target engine id to add

        """
        with self._target_engine_lock:
            self._target_engine_ids.add(target_engine_id)
        logger.info(
            f"Adding {target_engine_id} to target engine ids for producer "
            f"{self.producer_name}"
        )

    def remove_target_engine(self, target_engine_id: str) -> bool:
        """Remove a target engine from the producer.

        Args:
            target_engine_id (str): a target engine id to remove

        Returns:
            True if the target engine id exists and was removed
            False if the target engine id does not exist

        """
        with self._target_engine_lock:
            if target_engine_id not in self._target_engine_ids:
                return False
            self._target_engine_ids.remove(target_engine_id)
            return True

    def is_running(self) -> bool:
        """Check if the producer is running."""
        return self._running.is_set()

    def _wait_for_running_internal(self, shutdown_event):
        while not shutdown_event.is_set():
            if self._running.wait(0.1):
                break

    async def wait_for_running(self) -> None:
        """Block until the producer is running."""
        shutdown_event = threading.Event()
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, self._wait_for_running_internal, shutdown_event
            )
        except asyncio.CancelledError:
            shutdown_event.set()
            raise

    def get_target_engines(self) -> frozenset[str]:
        """Return the target engines for the producer."""
        with self._target_engine_lock:
            return frozenset(self._target_engine_ids)


class TokenPool:
    """A pool of tokens.

    Used to limit the number of in-flight requests for a particular
    input source.
    """

    def __init__(self, num_tokens: int, producer_id: str):
        """Initialize the token pool.

        Args:
            num_tokens (int): The number of tokens in the pool
            producer_id (str): The producer identifier

        """
        self._max_tokens = num_tokens
        self._sem = asyncio.BoundedSemaphore(num_tokens)
        self._producer_id = producer_id
        PRODUCER_TOKEN_COUNT.labels(producer_id=producer_id).set(
            self.get_remaining_tokens()
        )

    def return_token(self) -> None:
        """Return a token to the pool."""
        PRODUCER_TOKEN_COUNT.labels(producer_id=self._producer_id).inc()
        self._sem.release()

    async def get_token(self) -> None:
        """Acquire a token from the pool.

        Wait if necessary until a token is available.
        """
        logger.debug("Waiting for token")
        await self._sem.acquire()
        PRODUCER_TOKEN_COUNT.labels(producer_id=self._producer_id).set(
            self.get_remaining_tokens()
        )
        logger.debug("Token acquired")

    def is_locked(self) -> bool:
        """Check if the semaphore is locked."""
        return self._sem.locked()

    def reset_tokens(self) -> None:
        """Reset the number of tokens in the pool to the max number."""
        self._sem = asyncio.Semaphore(self._max_tokens)
        PRODUCER_TOKEN_COUNT.labels(producer_id=self._producer_id).set(
            self.get_remaining_tokens()
        )

    def get_remaining_tokens(self) -> int:
        """Return the number of remaining tokens in the pool."""
        return self._sem._value


class GabrielClient(ABC):
    """Abstract base class for a Gabriel client."""

    def __init__(self, prometheus_port: int):
        """Initialize the Gabriel client.

        Args:
            prometheus_port (int): Port for Prometheus metrics.
        """
        self._running = True
        # Whether a welcome message has been received from the server
        self._welcome_event = asyncio.Event()
        self.input_producers = set()
        # The number of tokens per input source, as specified by the \
        # server
        self._num_tokens_per_producer = None
        # Mapping from source id to tokens
        self._tokens = {}
        # Mapping from frame ids to the timestamp at which the input was
        # sent to the server
        self._pending_results = {}
        self._prometheus_port = prometheus_port

    def launch(self) -> None:
        """Launch the client synchronously.

        This method will block execution until the client is stopped.
        """
        asyncio.run(self.launch_async())

    @abstractmethod
    def launch_async(self) -> None:
        """Launch the client asynchronously."""
        pass

    def stop(self) -> None:
        """Stop the client."""
        self._running = False

    def record_send_metrics(self, from_client: FromClient) -> bool:
        """Record metrics related to sending of input to server."""
        producer_id = from_client.producer_id
        CLIENT_INPUTS_SENT_TOTAL.labels(producer_id=producer_id).inc()

        frame_id = from_client.frame_id
        self._pending_results[frame_id] = time.monotonic()

    def record_response_latency(self, result_wrapper: ToClient.ResultWrapper):
        """Record the response latency for input."""
        producer_id = result_wrapper.producer_id
        result = result_wrapper.result
        frame_id = result.frame_id

        send_time = self._pending_results[frame_id]
        latency = time.monotonic() - send_time

        CLIENT_INPUT_PROCESSING_LATENCY.labels(
            producer_id=producer_id
        ).observe(latency)
