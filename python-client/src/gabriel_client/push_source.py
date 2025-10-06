"""Push-based source that can be used to send frames to Gabriel server."""

import asyncio
import multiprocessing

from gabriel_protocol import gabriel_pb2

from gabriel_client.gabriel_client import InputProducer


def consumer(_):
    """A consumer that does nothing."""
    pass


class Source:
    """A push-based source used to send frames to Gabriel server."""

    def __init__(self, source_name, target_engine_ids):
        """Initialize the push-based source."""
        self._source_name = source_name
        self._frame_available = asyncio.Semaphore(0)
        self._latest_input_frame = None
        self._read, self._write = multiprocessing.Pipe(duplex=False)
        self._added_callback = False
        self._target_engine_ids = target_engine_ids

    def get_input_producer(self):
        """Returns an input producer for the source."""

        def reader_callback():
            input_frame = gabriel_pb2.InputFrame()
            input_frame.ParseFromString(self._read.recv_bytes())
            self._latest_input_frame = input_frame
            self._frame_available.release()

        async def producer():
            if not self._added_callback:
                # We need this to be run on the event loop running the producer
                fd = self._read.fileno()
                asyncio.get_event_loop().add_reader(fd, reader_callback)
                self._added_callback = True

            await self._frame_available.acquire()
            return self._latest_input_frame

        return InputProducer(
            producer=producer,
            target_engine_ids=self._target_engine_ids,
            source_name=self._source_name,
        )

    def send(self, input_frame):
        """Sends an input frame to the Gabriel server."""
        self._write.send_bytes(input_frame.SerializeToString())
