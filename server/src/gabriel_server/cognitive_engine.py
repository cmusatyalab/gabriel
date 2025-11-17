"""Abstract base class for cognitive engines and related utilities."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from gabriel_protocol import gabriel_pb2


@dataclass
class Result:
    """A result returned by a cognitive engine."""

    status: gabriel_pb2.Status
    # Only populated if status.code == SUCCESS
    payload_type: gabriel_pb2.PayloadType
    payload: Optional[Any] = None


class Engine(ABC):
    """Abstract class for cognitive engines."""

    @abstractmethod
    def handle(self, input_frame: gabriel_pb2.InputFrame) -> Result:
        """Process a single gabriel_pb2.InputFrame().

        Return an instance of Result.
        """
        pass
