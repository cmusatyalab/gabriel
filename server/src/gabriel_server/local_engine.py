"""Run a single cognitive engine.

Starts a local server and connects the engine to it.
"""

import asyncio
import logging
import multiprocessing
from typing import Optional

from gabriel_protocol import gabriel_pb2
from google.protobuf.any_pb2 import Any

from gabriel_server import cognitive_engine
from gabriel_server.websocket_server import WebsocketServer
from gabriel_server.zeromq_server import ZeroMQServer

logger = logging.getLogger(__name__)


class LocalEngine:
    """Runs a single cognitive engine with a local server."""

    def __init__(
        self,
        engine_factory,
        input_queue_maxsize: int,
        port: int,
        num_tokens: int,
        engine_id: str = "local_engine",
        message_max_size: int = None,
        use_zeromq: bool = False,
        ipc_path: Optional[str] = None,
    ):
        """Initialize the local engine.

        Args:
            engine_factory:
                A callable that returns a cognitive engine instance.
            input_queue_maxsize (int): The maximum size of the input queue.
            port (int): The port to run the server on.
            num_tokens (int): The number of tokens to allocate to the producer.
            engine_id (str): The id of the engine.
            message_max_size (int): The maximum size of a message in bytes.
            use_zeromq (bool):
                Whether to use ZeroMQ or WebSocket for communication.
            ipc_path (str, optional):
                If provided, use IPC with the given path instead of TCP.
        """
        self.engine_factory = engine_factory
        self.input_queue_maxsize = input_queue_maxsize
        self.port = port
        self.num_tokens = num_tokens
        self.message_max_size = message_max_size
        self.use_zeromq = use_zeromq
        self.ipc_path = ipc_path
        self.engine_id = engine_id

    def run(self):
        """Starts the local server and the cognitive engine synchronously."""
        asyncio.run(self.run_async())

    async def run_async(self):
        """Starts the local server and the cognitive engine."""
        self.engine_conn, server_conn = multiprocessing.Pipe()

        local_server = _LocalServer(
            self.num_tokens,
            self.input_queue_maxsize,
            server_conn,
            self.use_zeromq,
            self.engine_id,
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
            from_client = gabriel_pb2.FromClient()
            from_client.ParseFromString(self.engine_conn.recv_bytes())

            input_frame = from_client.input_frame

            result = engine.handle(input_frame)
            result_proto = gabriel_pb2.Result()
            result_proto.frame_id = from_client.frame_id
            result_proto.target_engine_id = self.engine_id

            if not isinstance(result, cognitive_engine.Result):
                error_msg = (
                    f"Incorrect type returned by engine. "
                    f"Expected a value of type cognitive_engine.Result, "
                    f"found {type(result)}"
                )
                logger.error(error_msg)
                result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
                result_proto.status.message = error_msg
                self.engine_conn.send_bytes(result_proto.SerializeToString())
                continue

            if not isinstance(result.status, gabriel_pb2.Status):
                error_msg = (
                    f"Return status not populated correctly by engine. "
                    f"Expected a value of type gabriel_pb2.Status, found "
                    f"{type(result.status)}"
                )
                logger.error(error_msg)
                result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
                result_proto.status.message = error_msg
                self.engine_conn.send_bytes(result_proto.SerializeToString())
                continue
            result_proto.status.CopyFrom(result.status)

            if result.status.code != gabriel_pb2.StatusCode.SUCCESS:
                logger.debug(f"{self.engine_id} sending error to server")
                self.engine_conn.send_bytes(result_proto.SerializeToString())
                continue

            if result.status.code == gabriel_pb2.StatusCode.SUCCESS:
                payload = result.payload

                if payload is None:
                    error_msg = "Engine did not specify result payload"
                    logger.error(error_msg)
                    result_proto.status.code = (
                        gabriel_pb2.StatusCode.ENGINE_ERROR
                    )
                    result_proto.status.message = error_msg
                    self.engine_conn.send_bytes(
                        result_proto.SerializeToString()
                    )
                    continue

                if isinstance(payload, str):
                    result_proto.string_result = payload
                elif isinstance(payload, bytes):
                    result_proto.bytes_result = payload
                elif isinstance(payload, Any):
                    result_proto.any_result.CopyFrom(payload)
                else:
                    error_msg = (
                        f"Engine produced unsupported result payload "
                        f"type: {type(payload)}"
                    )
                    logger.error(error_msg)
                    result_proto.status.code = (
                        gabriel_pb2.StatusCode.ENGINE_ERROR
                    )
                    result_proto.status.message = error_msg
                    self.engine_conn.send_bytes(
                        result_proto.SerializeToString()
                    )
                    continue

            logger.debug(f"{self.engine_id} sending result to server")
            self.engine_conn.send_bytes(result_proto.SerializeToString())


class _LocalServer:
    def __init__(
        self,
        num_tokens_per_producer,
        input_queue_maxsize,
        conn,
        use_zeromq,
        engine_id,
    ):
        self._input_queue = asyncio.Queue(input_queue_maxsize)
        self._conn = conn
        self._result_ready = asyncio.Event()
        self._engine_ids = {engine_id}
        self._server = (ZeroMQServer if use_zeromq else WebsocketServer)(
            num_tokens_per_producer, self._send_to_engine, self._engine_ids
        )
        self.engine_id = engine_id

    async def _send_to_engine(self, from_client, address):
        logger.debug("Received input from client %s", address)
        if self._input_queue.full():
            return (
                gabriel_pb2.StatusCode.SERVER_DROPPED_FRAME,
                "Input queue is full, dropping input",
            )

        self._input_queue.put_nowait((from_client, address))
        return (gabriel_pb2.StatusCode.SUCCESS, "")

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
            from_client, address = await self._input_queue.get()
            await loop.run_in_executor(
                None,
                self._conn.send_bytes,
                from_client.SerializeToString(),
            )
            result = gabriel_pb2.Result()

            await self._result_ready.wait()
            self._result_ready.clear()

            # Get the result from the engine
            data = await loop.run_in_executor(None, self._conn.recv_bytes)
            result.ParseFromString(data)

            await self._server.send_result(
                address,
                from_client.producer_id,
                self.engine_id,
                result,
                return_token=True,
            )
