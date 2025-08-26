import asyncio
import logging
import multiprocessing
import os
from gabriel_protocol import gabriel_pb2
from gabriel_server.websocket_server import WebsocketServer
from gabriel_server.zeromq_server import ZeroMQServer


logger = logging.getLogger(__name__)


async def run_async(engine_factory, source_name, input_queue_maxsize, port, num_tokens,
        message_max_size=None, use_zeromq=False, ipc_path=None):
    engine_conn, server_conn = multiprocessing.Pipe()

    local_server = _LocalServer(num_tokens, input_queue_maxsize, server_conn, source_name, use_zeromq)

    engine_process = multiprocessing.Process(
        target=_run_engine, args=(engine_factory, engine_conn))
    engine_process.start()
    try:
        await local_server.launch_async(port if not ipc_path else ipc_path, message_max_size, use_ipc=(ipc_path != None))
    except asyncio.exceptions.CancelledError:
        raise Exception('Server stopped')

def run(engine_factory, source_name, input_queue_maxsize, port, num_tokens,
        message_max_size=None, use_zeromq=False, ipc_path=None):
    engine_conn, server_conn = multiprocessing.Pipe()

    local_server = _LocalServer(num_tokens, input_queue_maxsize, server_conn, source_name, use_zeromq)

    engine_process = multiprocessing.Process(
        target=_run_engine, args=(engine_factory, engine_conn))
    engine_process.start()
    local_server.launch(port if not ipc_path else ipc_path, message_max_size, use_ipc=(ipc_path != None))

    raise Exception('Server stopped')

class _LocalServer:
    def __init__(self, num_tokens_per_source, input_queue_maxsize, conn, source_name, use_zeromq):
        self._input_queue = asyncio.Queue(input_queue_maxsize)
        self._conn = conn
        self._result_ready = asyncio.Event()
        self._server = (
            ZeroMQServer if use_zeromq else WebsocketServer)(num_tokens_per_source, self._send_to_engine)
        self._server.add_source_consumed(source_name)

    async def _send_to_engine(self, from_client, address):
        if self._input_queue.full():
            return False

        self._input_queue.put_nowait((from_client, address))
        return True

    def launch(self, port_or_path, message_max_size, use_ipc=False):
        asyncio.get_event_loop().add_reader(
            self._conn.fileno(), self._result_ready.set)
        asyncio.ensure_future(self._engine_comm())
        self._server.launch(port_or_path, message_max_size, use_ipc=use_ipc)

    async def launch_async(self, port_or_path, message_max_size, use_ipc=False):
        await self._server.launch_async(port_or_path, message_max_size, use_ipc=use_ipc)

    async def _engine_comm(self):
        await self._server.wait_for_start()
        while self._server.is_running():
            from_client, address = await self._input_queue.get()
            self._conn.send_bytes(from_client.input_frame.SerializeToString())
            result_wrapper = gabriel_pb2.ResultWrapper()

            while not self._conn.poll():
                await self._result_ready.wait()
                self._result_ready.clear()

            result_wrapper.ParseFromString(self._conn.recv_bytes())
            await self._server.send_result_wrapper(
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
