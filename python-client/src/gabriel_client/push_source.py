import asyncio
import multiprocessing
from gabriel_protocol import gabriel_pb2
from gabriel_client.websocket_client import ProducerWrapper


def consumer(_):
    pass


class Source:
    def __init__(self, source_name):
        self._source_name = source_name
        self._frame_available = asyncio.Semaphore(0)
        self._latest_input_frame = None
        self._read, self._write = multiprocessing.Pipe(duplex=False)
        self._added_callback = False

    def get_producer_wrapper(self):
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

        return ProducerWrapper(producer=producer, source_name=self._source_name)

    def send(self, input_frame):
        self._write.send_bytes(input_frame.SerializeToString())
