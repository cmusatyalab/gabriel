from abc import ABC, abstractmethod
import asyncio
import uuid
import logging

logger = logging.getLogger(__name__)


class InputProducer:
    """
    An input producer that produces inputs for the client to send to the server.
    """

    def __init__(
        self, producer, target_engine_ids: list[str], source_name: str = None
    ):
        """
        Args:
            producer (callable): A callable that produces input data
            target_engine_ids (list[str]): A list of target engine IDs for the input
            source_name (str, optional): The name of the source producing the input
        """
        self._running = asyncio.Event()
        self._running.set()
        self._producer = producer
        self.target_engine_ids = target_engine_ids
        self.source_id = (
            source_name + "-" + str(uuid.uuid4())
            if source_name
            else str(uuid.uuid4())
        )

    async def produce(self):
        """
        Invokes the producer to generate input
        """
        if not self._running:
            raise Exception("Producer called when not running")
        res = await self._producer()
        return res

    def start(self, target_engine_ids: list[str]):
        """
        Starts the producer

        Args:
            target_engine_ids (list[str]): A list of target engine IDs for the input
        """
        if self._running.is_set():
            raise Exception("Producer already started")
        self.target_engine_ids = target_engine_ids
        self._running.set()
        logger.info(
            f"Starting producer and targeting engines {target_engine_ids}"
        )

    def stop(self):
        """
        Stops the producer
        """
        logger.info("Stopping producer")
        self._running.clear()

    def is_running(self):
        """
        Checks if the producer is running
        """
        return self._running

    async def wait_for_running(self):
        """
        Wait until the producer is running
        """
        await self._running.wait()


class TokenPool:
    """
    A pool of tokens used to limit the number of in-flight requests for
    a particular input source.
    """

    def __init__(self, num_tokens):
        self._max_tokens = num_tokens
        self._num_tokens = num_tokens
        self._sem = asyncio.BoundedSemaphore(num_tokens)

    def return_token(self):
        """
        Returns a token to the pool.
        """
        self._sem.release()

    async def get_token(self):
        """
        Acquires a token from the pool, waiting if necessary until a
        token is available.
        """
        logger.debug("Waiting for token")
        await self._sem.acquire()
        logger.debug("Token acquired")

    def is_locked(self):
        """
        Checks if the semaphore is locked.
        """
        return self._sem.locked()

    def reset_tokens(self):
        """
        Resets the number of tokens in the pool to the maximum number of tokens.
        """
        self._sem = asyncio.Semaphore(self._max_tokens)
        self._num_tokens = self._max_tokens

    def get_remaining_tokens(self):
        """
        Returns the number of remaining tokens in the pool.
        """
        return self._sem._value


class GabrielClient(ABC):
    def __init__(self):
        self._running = True
        # Whether a welcome message has been received from the server
        self._welcome_event = asyncio.Event()
        self.input_producers = set()
        # The number of tokens per input source, as specified by the \
        # server
        self._num_tokens_per_source = None
        # Mapping from source id to tokens
        self._tokens = dict()

    @abstractmethod
    def launch(self):
        """
        Launch the client synchronously. This method will block execution until
        the client is stopped.
        """
        pass

    @abstractmethod
    def launch_async(self):
        """
        Launch the client asynchronously.
        """
        pass

    def register_input_producer(self, producer: InputProducer):
        """
        Register an input producer with the client.

        Args:
            producer (InputProducer): The input producer to register
        """
        self.input_producers.add(producer)

    def deregister_input_producer(self, producer: InputProducer):
        """
        Deregister an input producer with the client.

        Args:
            producer (InputProducer): The input producer to deregister
        """
        self.input_producers.remove(producer)

    def stop(self):
        """
        Stop the client
        """
        self._running = False
