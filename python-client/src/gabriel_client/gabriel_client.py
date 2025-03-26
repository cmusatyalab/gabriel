from abc import ABC, abstractmethod
import asyncio
from collections import namedtuple
from gabriel_client.token_bucket import _TokenBucket
import logging
from gabriel_protocol import gabriel_pb2

logger = logging.getLogger(__name__)

# Represents an input that this client produces. 'producer' is the method that
# produces inputs. 'token_bucket' is the token bucket to use for the input.
# 'target_computation_types' is a list of computations to be performed on this
# input.
ProducerWrapper = namedtuple('ProducerWrapper', ['producer', 'token_bucket', 'target_computation_types'])

class GabrielClient(ABC):

    def __init__(self, host, port, producer_wrappers, consumer, uri_format):
        # Whether a welcome message has been received from the server
        self._welcome_event = asyncio.Event()
        # The token buckets for the inputs
        self._token_buckets = {}
        # The computation types supported by the server
        self._computations = []
        self._running = True
        self._uri = uri_format.format(host=host, port=port)
        self.producer_wrappers = producer_wrappers
        self.consumer = consumer

    @abstractmethod
    def launch(self, message_max_size=None):
        pass

    def stop(self):
        self._running = False
        logger.info('stopping server')

    def _process_welcome(self, welcome):
        """
        Process a welcome message received from the server.

        Args:
            welcome:
                The gabriel_pb2.ToClient.Welcome message received from the
                server
        """
        logger.info(f"The server can perform {len(welcome.computations_supported)} computations")
        for producer_wrapper in self.producer_wrappers:
            token_bucket = producer_wrapper.token_bucket
            logger.info(f"Tokens available for {token_bucket}={welcome.num_tokens_per_bucket}")
            token_bucket = producer_wrapper.token_bucket
            self._token_buckets[token_bucket] = _TokenBucket(welcome.num_tokens_per_bucket)
        self._computations = welcome.computations_supported
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
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error processing response from server: {e}")
        elif (result_wrapper.status ==
              gabriel_pb2.ResultWrapper.NO_ENGINE_FOR_COMPUTATION):
            raise Exception('No engine for computation')
        else:
            status = result_wrapper.Status.Name(result_wrapper.status)
            logger.error(f'Output status was: {status}')

        if response.return_token:
            self._token_buckets[response.token_bucket].return_token()
