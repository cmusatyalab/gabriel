"""Abstract base class for cognitive engines and related utilities."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from gabriel_protocol.v1 import gabriel_pb2
from google.protobuf.any_pb2 import Any as ProtoAny


@dataclass
class Result:
    """A result returned by a cognitive engine."""

    status: gabriel_pb2.Status
    payload: Optional[Any] = None


class Engine(ABC):
    """Abstract class for cognitive engines."""

    @abstractmethod
    def handle(
        self, input_frame: gabriel_pb2.InputFrame, client_info: ProtoAny
    ) -> Result:
        """Process a single gabriel_pb2.InputFrame().

        Args:
            input_frame: The input to process.
            client_info: The Any registered by the producing client's
                Registration message, or an empty Any if it registered none.

        Return an instance of Result.
        """
        pass
