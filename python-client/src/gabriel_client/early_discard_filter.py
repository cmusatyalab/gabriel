import asyncio
import os
from gabriel_protocol import gabriel_pb2
from gabriel_client.websocket_client import ProducerWrapper


_NUM_BYTES_FOR_SIZE = 4
_BYTEORDER = 'big'


def consumer():
    pass


class Source:
    def __init__(self, source_name):
        self._source_name = source_name
        self._frame_available = asyncio.Event()
        self._latest_input_frame = None

        self._constructor_pid = os.getpid()
        self._read, self._write = os.pipe()
        self._started_receiver = False

    def get_producer_wrapper(self):
        async def receiver():
            stream_reader = asyncio.StreamReader()
            def protocol_factory():
                return asyncio.StreamReaderProtocol(stream_reader)
            transport = await asyncio.get_event_loop().connect_read_pipe(
                protocol_factory, os.fdopen(self._read, mode='r'))

            # TODO do we need to close transport when filter gets garbage
            # collected?

            while True:
                size_bytes = await stream_reader.readexactly(
                    _NUM_BYTES_FOR_SIZE)
                size_of_message = int.from_bytes(size_bytes, _BYTEORDER)

                input_frame = gabriel_pb2.InputFrame()
                input_frame.ParseFromString(
                    await stream_reader.readexactly(size_of_message))
                self._latest_input_frame = input_frame
                self._frame_available.set()

        async def producer():
            if not self._started_receiver:
                self._started_receiver = True
                assert os.getpid() == self._constructor_pid
                asyncio.ensure_future(receiver())

            await self._frame_available.wait()

            # Clear because we are sending self._latest_input_frame
            self._frame_available.clear()

            return self._latest_input_frame

        return ProducerWrapper(producer=producer, source_name=self._source_name)

    def send(self, input_frame):
        serialized_message = input_frame.SerializeToString()

        size_of_message = len(serialized_message)
        size_bytes = size_of_message.to_bytes(_NUM_BYTES_FOR_SIZE, _BYTEORDER)

        num_bytes_written = os.write(self._write, size_bytes)
        assert num_bytes_written == _NUM_BYTES_FOR_SIZE, 'Write incomplete'

        num_bytes_written = os.write(self._write, serialized_message)
        assert num_bytes_written == size_of_message, 'Write incomplete'
