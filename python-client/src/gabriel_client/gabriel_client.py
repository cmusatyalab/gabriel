"""Abstract base class for a Gabriel client and related classes."""

import asyncio
import logging
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any, Union

from gabriel_protocol.gabriel_pb2 import InputFrame

logger = logging.getLogger(__name__)


class InputProducer:
    """An input producer that produces inputs to send to the server.

    The methods of this class are thread-safe.
    """

    def __init__(
        self,
        producer: Callable[[], Coroutine[Any, Any, InputFrame | None]],
        target_engine_ids: list[str],
        source_name: Union[str, None] = None,
    ):
        """Initialize the input producer.

        Args:
            producer (coroutine function):
                A coroutine function that produces input data
            target_engine_ids (list[str]):
                A list of target engine IDs for the input
            source_name (str, optional):
                The name of the source producing the input

        """
        self._running = threading.Event()
        self._running.set()
        self._producer = producer
        self._target_engine_ids = target_engine_ids
        self._target_engine_lock = threading.Lock()
        self.source_id = (
            source_name + "-" + str(uuid.uuid4())
            if source_name
            else str(uuid.uuid4())
        )
        self._loop = None

    async def produce(self):
        """Invoke the producer to generate input."""
        if not self._running.is_set():
            raise Exception("Producer called when not running")
        res = await self._producer()
        return res

    def start(self, target_engine_ids: list[str]):
        """Start the producer.

        Args:
            target_engine_ids (list[str]):
                A list of target engine IDs for the input

        """
        if self._running.is_set():
            raise Exception("Producer already started")
        with self._target_engine_lock:
            self._target_engine_ids = target_engine_ids
        self._running.set()
        logger.info(
            f"Starting producer and targeting engines {target_engine_ids}"
        )

    def stop(self):
        """Stop the producer."""
        logger.info("Stopping producer")
        self._running.clear()

    def change_target_engines(self, target_engine_ids: list[str]):
        """Change the target engines for the producer.

        Args:
            target_engine_ids (list[str]):
                A list of target engine IDs for the input

        """
        with self._target_engine_lock:
            self._target_engine_ids = target_engine_ids
        logger.info(f"Changing target engines to {target_engine_ids}")

    def is_running(self):
        """Check if the producer is running."""
        return self._running.is_set()

    def _wait_for_running_internal(self, shutdown_event):
        while not shutdown_event.is_set():
            self._running.wait(0.1)

    async def wait_for_running(self):
        """Wait until the producer is running."""
        shutdown_event = threading.Event()
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, self._wait_for_running_internal, shutdown_event
            )
        except asyncio.CancelledError:
            shutdown_event.set()
            raise

    def get_target_engines(self):
        """Return the target engines for the producer."""
        with self._target_engine_lock:
            return self._target_engine_ids


class TokenPool:
    """A pool of tokens.

    Used to limit the number of in-flight requests for a particular
    input source.
    """

    def __init__(self, num_tokens):
        """Initialize the token pool.

        Args:
            num_tokens (int): The number of tokens in the pool

        """
        self._max_tokens = num_tokens
        self._num_tokens = num_tokens
        self._sem = asyncio.BoundedSemaphore(num_tokens)

    def return_token(self):
        """Return a token to the pool."""
        self._sem.release()

    async def get_token(self):
        """Acquire a token from the pool.

        Wait if necessary until a token is available.
        """
        logger.debug("Waiting for token")
        await self._sem.acquire()
        logger.debug("Token acquired")

    def is_locked(self):
        """Check if the semaphore is locked."""
        return self._sem.locked()

    def reset_tokens(self):
        """Reset the number of tokens in the pool to the max number."""
        self._sem = asyncio.Semaphore(self._max_tokens)
        self._num_tokens = self._max_tokens

    def get_remaining_tokens(self):
        """Return the number of remaining tokens in the pool."""
        return self._sem._value


class GabrielClient(ABC):
    """Abstract base class for a Gabriel client."""

    def __init__(self):
        """Initialize the Gabriel client."""
        self._running = True
        # Whether a welcome message has been received from the server
        self._welcome_event = asyncio.Event()
        self.input_producers = set()
        # The number of tokens per input source, as specified by the \
        # server
        self._num_tokens_per_source = None
        # Mapping from source id to tokens
        self._tokens = {}

    @abstractmethod
    def launch(self):
        """Launch the client synchronously.

        This method will block execution until the client is stopped.
        """
        pass

    @abstractmethod
    def launch_async(self):
        """Launch the client asynchronously."""
        pass

    def stop(self):
        """Stop the client."""
        self._running = False
