"""Abstract base class for cognitive engines and related utilities."""

from abc import ABC, abstractmethod

from gabriel_protocol import gabriel_pb2


def create_result_wrapper(status):
    """Create a ResultWrapper with the given status."""
    result_wrapper = gabriel_pb2.ResultWrapper()
    result_wrapper.status = status
    return result_wrapper


def unpack_extras(extras_class, input_frame):
    """Unpack extras from input_frame into an instance of extras_class."""
    extras = extras_class()
    input_frame.extras.Unpack(extras)
    return extras


class Engine(ABC):
    """Abstract class for cognitive engines."""

    @abstractmethod
    def handle(self, input_frame) -> gabriel_pb2.ResultWrapper:
        """Process a single gabriel_pb2.InputFrame().

        Return an instance of gabriel_pb2.ResultWrapper().
        """
        pass
