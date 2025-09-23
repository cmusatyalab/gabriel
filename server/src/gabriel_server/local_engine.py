"""
Run a single cognitive engine. Starts a local server and connects the engine to it.
"""

from typing import Optional
import asyncio
import logging
import multiprocessing
from gabriel_protocol import gabriel_pb2
from gabriel_server.websocket_server import WebsocketServer
from gabriel_server.zeromq_server import ZeroMQServer


logger = logging.getLogger(__name__)


class LocalEngine:
    def __init__(
        self,
        engine_factory,
        input_queue_maxsize: int,
        port: int,
        num_tokens: int,
        engine_name: str = "local_engine",
        message_max_size: int = None,
        use_zeromq: bool = False,
        ipc_path: Optional[str] = None,
    ):
        """
        Args:
            engine_factory: A callable that returns a cognitive engine instance.
            input_queue_maxsize (int): The maximum size of the input queue.
            port (int): The port to run the server on.
            num_tokens (int): The number of tokens to allocate to the source.
            engine_name (str): The name of the engine.
            message_max_size (int): The maximum size of a message in bytes.
            use_zeromq (bool): Whether to use ZeroMQ or WebSocket for communication.
            ipc_path (str, optional): If provided, use IPC with the given path instead of TCP.
        """
        self.engine_factory = engine_factory
        self.input_queue_maxsize = input_queue_maxsize
        self.port = port
        self.num_tokens = num_tokens
        self.message_max_size = message_max_size
        self.use_zeromq = use_zeromq
        self.ipc_path = ipc_path
        self.engine_name = engine_name

    def run(self):
        asyncio.run(self.run_async())

    async def run_async(self):
        """
        Starts the local server and the cognitive engine.
        """
        self.engine_conn, server_conn = multiprocessing.Pipe()

        local_server = _LocalServer(
            self.num_tokens,
            self.input_queue_maxsize,
            server_conn,
            self.use_zeromq,
        )

        engine_process = multiprocessing.Process(target=self._run_engine)
        engine_process.start()

        try:
            await local_server.launch_async(
                self.port if not self.ipc_path else self.ipc_path,
                self.message_max_size,
                use_ipc=(self.ipc_path is not None),
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            engine_process.terminate()
            engine_process.join()
            raise

        raise Exception("Server stopped")

    def _run_engine(self):
        engine = self.engine_factory()
        logger.info("Cognitive engine started")
        while True:
            input_frame = gabriel_pb2.InputFrame()
            logger.info("Engine waiting for input frame from server")
            input_frame.ParseFromString(self.engine_conn.recv_bytes())
            logger.info("Engine received input frame from server")

            result_wrapper = engine.handle(input_frame)
            self.engine_conn.send_bytes(result_wrapper.SerializeToString())
            logger.info("Sent result wrapper to server")


class _LocalServer:
    def __init__(
        self, num_tokens_per_source, input_queue_maxsize, conn, use_zeromq
    ):
        self._input_queue = asyncio.Queue(input_queue_maxsize)
        self._conn = conn
        self._result_ready = asyncio.Event()
        self._server = (ZeroMQServer if use_zeromq else WebsocketServer)(
            num_tokens_per_source, self._send_to_engine
        )

    async def _send_to_engine(self, from_client, address):
        logger.info("Received input from client %s", address)
        if self._input_queue.full():
            return False

        self._input_queue.put_nowait((from_client, address))
        return True

    def launch(self, port_or_path, message_max_size, use_ipc=False):
        asyncio.run(
            self.launch_async(port_or_path, message_max_size, use_ipc=use_ipc)
        )

    async def launch_async(
        self, port_or_path, message_max_size, use_ipc=False
    ):
        logger.info(f"Starting local server on port {port_or_path}")
        asyncio.get_event_loop().add_reader(
            self._conn.fileno(), self._result_ready.set
        )
        comm_task = asyncio.create_task(self._engine_comm())
        server_task = asyncio.create_task(
            self._server.launch_async(
                port_or_path, message_max_size, use_ipc=use_ipc
            )
        )
        await asyncio.gather(comm_task, server_task)

    async def _engine_comm(self):
        await self._server.wait_for_start()
        loop = asyncio.get_running_loop()
        while self._server.is_running():
            logger.info("Waiting for input from client")
            from_client, address = await self._input_queue.get()
            await loop.run_in_executor(
                None,
                self._conn.send_bytes,
                from_client.input_frame.SerializeToString(),
            )
            logger.info("Sent input frame to engine")
            result_wrapper = gabriel_pb2.ResultWrapper()

            await self._result_ready.wait()
            self._result_ready.clear()

            logger.info("Receiving result from engine")
            data = await loop.run_in_executor(None, self._conn.recv_bytes)
            logger.info("Received result from engine")
            result_wrapper.ParseFromString(data)
            await self._server.send_result_wrapper(
                address,
                from_client.source_id,
                from_client.frame_id,
                "local_engine",
                result_wrapper,
                return_token=True,
            )
