import asyncio
import logging
import multiprocessing
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.websocket_server import WebsocketServer


logger = logging.getLogger(__name__)


def run(engine_factory, source_name, input_queue_maxsize, port, num_tokens,
        message_max_size=None):
    server_conn, engine_conn = multiprocessing.Pipe(duplex=True)

    local_server = _LocalServer(num_tokens, input_queue_maxsize, server_conn)
    local_server.add_source_consumed(source_name)

    engine_process = multiprocessing.Process(
        target=_run_engine, args=(engine_factory, engine_conn))
    engine_process.start()

    local_server.launch(port, message_max_size)

    raise Exception('Server stopped')


class _LocalServer(WebsocketServer):
    def __init__(self, num_tokens_per_source, input_queue_maxsize, conn):
        super().__init__(num_tokens_per_source)
        self._input_queue = queue.Queue(input_queue_maxsize)
        self._conn = conn

    async def _send_to_engine(self, from_client, address):
        if self._input_queue.full():
            return False

        self._input_queue.put_nowait((from_client, address))
        return True

    def launch(self, port, message_max_size):
        asyncio.ensure_future(self._engine_comm())
        super().launch(port, message_max_size)

    async def _engine_comm(self):
        while self.is_running():
            from_client, address = await self._input_queue.get()
            self._conn.send(from_client)
            asyncio.get_event_loop().add_reader
            result_wrapper = self._conn.recv()  # TODO make this nonblocking with add_reader
            self.send_result_wrapper(
                address, from_client.source_name, from_client.frame_id,
                return_token=True, result_wrapper)


def _run_engine(engine_factory, input_queue, read, write):
    try:
        os.close(read)

        engine = engine_factory()
        logger.info('Cognitive engine started')
        while True:
            to_engine = gabriel_pb2.ToEngine()
            to_engine.ParseFromString(input_queue.get())

            result_wrapper = engine.handle(to_engine.from_client)
            result_wrapper.source_name = to_engine.from_client.source_name
            result_wrapper.return_token = True

            from_engine = cognitive_engine.pack_from_engine(
                to_engine.host, to_engine.port, result_wrapper)
            serialized_message = from_engine.SerializeToString()
            message_size = len(serialized_message)
            size_bytes = message_size.to_bytes(_NUM_BYTES_FOR_SIZE, _BYTEORDER)

            num_bytes_written = os.write(fd, size_bytes)
            assert num_bytes_written == _NUM_BYTES_FOR_SIZE, 'Write incomplete'

            num_bytes_written = os.write(fd, serialized_message)
            assert num_bytes_written == message_size, 'Write incomplete'
    finally:
        os.close(write)
