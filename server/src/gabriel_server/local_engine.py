import asyncio
import logging
import multiprocessing
import os
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.websocket_server import WebsocketServer


_NUM_BYTES_FOR_SIZE = 4
_BYTEORDER = 'big'


logger = logging.getLogger(__name__)


def run(engine_factory, source_name, input_queue_maxsize, port, num_tokens,
        message_max_size=None):
    engine_read, server_write = multiprocessing.Pipe(duplex=False)

    # We cannot read from multiprocessing.Pipe without blocking the event
    # loop
    server_read, engine_write = os.pipe()

    local_server = _LocalServer(
        num_tokens, input_queue_maxsize, server_write, server_read)
    local_server.add_source_consumed(source_name)

    engine_process = multiprocessing.Process(
        target=_run_engine,
        args=(engine_factory, engine_read, server_read, engine_write))
    try:
        engine_process.start()
        os.close(engine_write)
        local_server.launch(port, message_max_size)
    finally:
        local_server.cleanup()
        os.close(server_read)

    raise Exception('Server stopped')


class _LocalServer(WebsocketServer):
    def __init__(self, num_tokens_per_source, input_queue_maxsize, write, read):
        super().__init__(num_tokens_per_source)
        self._input_queue = asyncio.Queue(input_queue_maxsize)
        self._write = write

        loop = asyncio.get_event_loop()
        self._stream_reader = asyncio.StreamReader()
        def protocol_factory():
            return asyncio.StreamReaderProtocol(self._stream_reader)
        pipe = os.fdopen(read, mode='r')
        self._transport, _ = loop.run_until_complete(
            loop.connect_read_pipe(protocol_factory, pipe))

    def cleanup(self):
        self._transport.close()

    async def _send_to_engine(self, from_client, address):
        if self._input_queue.full():
            return False

        self._input_queue.put_nowait((from_client, address))
        return True

    def launch(self, port, message_max_size):
        asyncio.ensure_future(self._engine_comm())
        super().launch(port, message_max_size)

    async def _engine_comm(self):
        await self.wait_for_start()
        while self.is_running():
            from_client, address = await self._input_queue.get()
            self._write.send(from_client.input_frame.SerializeToString())

            size_bytes = await self._stream_reader.readexactly(
                _NUM_BYTES_FOR_SIZE)
            size_of_message = int.from_bytes(size_bytes, _BYTEORDER)
            result_wrapper_serialized = await self._stream_reader.readexactly(
                size_of_message)

            result_wrapper = gabriel_pb2.ResultWrapper()
            result_wrapper.ParseFromString(result_wrapper_serialized)
            await self.send_result_wrapper(
                address, from_client.source_name, from_client.frame_id,
                result_wrapper, return_token=True)


def _run_engine(engine_factory, engine_read, server_read, engine_write):
    try:
        os.close(server_read)

        engine = engine_factory()
        logger.info('Cognitive engine started')
        while True:
            input_frame = gabriel_pb2.InputFrame()
            input_frame.ParseFromString(engine_read.recv())

            result_wrapper = engine.handle(input_frame)
            serialized_message = result_wrapper.SerializeToString()
            message_size = len(serialized_message)
            size_bytes = message_size.to_bytes(_NUM_BYTES_FOR_SIZE, _BYTEORDER)

            num_bytes_written = os.write(engine_write, size_bytes)
            assert num_bytes_written == _NUM_BYTES_FOR_SIZE, 'Write incomplete'

            num_bytes_written = os.write(engine_write, serialized_message)
            assert num_bytes_written == message_size, 'Write incomplete'
    finally:
        os.close(engine_write)
