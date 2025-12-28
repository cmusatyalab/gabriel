"""Manages results returned by cognitive engines."""

import logging
from abc import ABC, abstractmethod
from typing import Union

import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2

logger = logging.getLogger(__name__)


class ResultSink(ABC):
    """Abstract base class for result sinks."""

    @abstractmethod
    async def process_result(self, result: gabriel_pb2.Result):
        """Process the engine result."""
        pass


class ZeroMQSink(ResultSink):
    """Publishes results to a ZeroMQ socket for consumption."""

    def __init__(self, port_or_path: Union[int, str]):
        """Initialize the ZeroMQ result sink.

        Args:
            port_or_path (int | str):
                The bind port or the bind unix socket path
        """
        self._context = zmq.asyncio.Context()
        self._sock = self.context.socket(zmq.PUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        if isinstance(port_or_path, int):
            self._sock.bind(f"tcp://*:{port_or_path}")
        else:
            self._sock.bind(f"ipc://{port_or_path}")

    async def process_result(self, result):
        """Process the engine result."""
        await self._sock.send(result.SerializeToString())


class ResultManager:
    """Manages result sinks."""

    def __init__(self):
        """Initialize internal data structures."""
        self._sinks = set()

    def register_result_sink(self, result_sink: ResultSink):
        """Register a sink to send engine results to."""
        self._sinks.add(result_sink)

    async def process_result(self, result: gabriel_pb2.Result):
        """Process an engine result."""
        if result.status.code != gabriel_pb2.StatusCode.SUCCESS:
            return
        for sink in self._sinks:
            await sink.process_result(result)
