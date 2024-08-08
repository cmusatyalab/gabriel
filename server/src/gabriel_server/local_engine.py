import asyncio
import logging
import multiprocessing
import os
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.websocket_server import WebsocketServer
from gabriel_server.zeromq_server import ZeroMQServer


logger = logging.getLogger(__name__)


def run(engine_factory, source_name, input_queue_maxsize, port, num_tokens,
        message_max_size=None, use_zeromq=False):
    engine_conn, server_conn = multiprocessing.Pipe()

    local_server = None
    local_server = (_ZeroMQLocalServer if use_zeromq else _LocalServer)(
        num_tokens, input_queue_maxsize, server_conn)
    local_server.add_source_consumed(source_name)

    engine_process = multiprocessing.Process(
        target=_run_engine, args=(engine_factory, engine_conn))
    engine_process.start()
    local_server.launch(port, message_max_size)

    raise Exception('Server stopped')


class _ZeroMQLocalServer(ZeroMQServer):
    def __init__(self, num_tokens_per_source, input_queue_maxsize, conn):
        super().__init__(num_tokens_per_source)
        self._input_queue = asyncio.Queue(input_queue_maxsize)
        self._conn = conn
        self._result_ready = asyncio.Event()

    async def _send_to_engine(self, from_client, address):
        if self._input_queue.full():
            return False

        self._input_queue.put_nowait((from_client, address))
        return True

    def launch(self, port, message_max_size):
        asyncio.get_event_loop().add_reader(
            self._conn.fileno(), self._result_ready.set)
        asyncio.ensure_future(self._engine_comm())
        super().launch(port, message_max_size)

    async def _engine_comm(self):
        await self.wait_for_start()
        while self.is_running():
            from_client, address = await self._input_queue.get()
            self._conn.send_bytes(from_client.input_frame.SerializeToString())
            result_wrapper = gabriel_pb2.ResultWrapper()

            while not self._conn.poll():
                await self._result_ready.wait()
                self._result_ready.clear()

            result_wrapper.ParseFromString(self._conn.recv_bytes())
            await self.send_result_wrapper(
                address, from_client.source_name, from_client.frame_id,
                result_wrapper, return_token=True)

class _LocalServer(WebsocketServer):
    def __init__(self, num_tokens_per_source, input_queue_maxsize, conn):
        super().__init__(num_tokens_per_source)
        self._input_queue = asyncio.Queue(input_queue_maxsize)
        self._conn = conn
        self._result_ready = asyncio.Event()

    async def _send_to_engine(self, from_client, address):
        if self._input_queue.full():
            return False

        self._input_queue.put_nowait((from_client, address))
        return True

    def launch(self, port, message_max_size):
        asyncio.get_event_loop().add_reader(
            self._conn.fileno(), self._result_ready.set)
        asyncio.ensure_future(self._engine_comm())
        super().launch(port, message_max_size)

    async def _engine_comm(self):
        await self.wait_for_start()
        while self.is_running():
            from_client, address = await self._input_queue.get()
            self._conn.send_bytes(from_client.input_frame.SerializeToString())
            result_wrapper = gabriel_pb2.ResultWrapper()

            while not self._conn.poll():
                await self._result_ready.wait()
                self._result_ready.clear()

            result_wrapper.ParseFromString(self._conn.recv_bytes())
            await self.send_result_wrapper(
                address, from_client.source_name, from_client.frame_id,
                result_wrapper, return_token=True)


def _run_engine(engine_factory, conn):
    engine = engine_factory()
    logger.info('Cognitive engine started')
    while True:
        input_frame = gabriel_pb2.InputFrame()
        input_frame.ParseFromString(conn.recv_bytes())

        result_wrapper = engine.handle(input_frame)
        conn.send_bytes(result_wrapper.SerializeToString())
