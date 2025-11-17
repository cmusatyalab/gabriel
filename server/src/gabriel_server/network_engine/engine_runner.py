"""Engine runner that connects to the server.

Handles communication between the cognitive engine and the server.
"""

import asyncio
import logging

import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2
from google.protobuf.any_pb2 import Any

from gabriel_server import network_engine

TEN_SECONDS = 10000
REQUEST_RETRIES = 3

logger = logging.getLogger(__name__)


class EngineRunner:
    """Connects a cognitive engine to the server.

    Client inputs are sent to the cognitive engine if they specify a target
    engine id that matches the engine id specified in :meth:`__init__`.
    """

    def __init__(
        self,
        engine,
        engine_id: str,
        server_address: str,
        all_responses_required: bool = False,
        timeout: int = TEN_SECONDS,
        request_retries: int = REQUEST_RETRIES,
    ):
        """Initializes the engine runner.

        Args:
            engine:
                The cognitive engine instance to run, must have a handle()
                method.
            engine_id (str): The identifier of the engine.
            server_address (str): The address of the server to connect to.
            all_responses_required (bool):
                Whether all responses are required from the engine.
            timeout (int):
                The timeout in milliseconds to wait for a response from the
                server.
            request_retries (int):
                The number of times to retry connecting to the server.
        """
        self.engine = engine
        self.engine_id = engine_id
        self.server_address = server_address
        self.all_responses_required = all_responses_required
        self.timeout = timeout
        self.request_retries = request_retries
        self.running = True
        self.done_event = asyncio.Event()

    def run(self):
        """Connects to the server and starts listening to messages."""
        asyncio.run(self.run_async())

    async def run_async(self):
        """Connects to the server and starts listening to messages."""
        context = zmq.asyncio.Context()

        while self.running and self.request_retries > 0:
            socket = context.socket(zmq.REQ)
            socket.connect(self.server_address)
            from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
            from_standalone_engine.welcome.engine_id = self.engine_id
            from_standalone_engine.welcome.all_responses_required = (
                self.all_responses_required
            )
            await socket.send(from_standalone_engine.SerializeToString())
            logger.info(
                f"{self.engine_id} sent welcome message to server "
                f"{self.server_address}"
            )

            while self.running:
                if await socket.poll(self.timeout) == 0:
                    logger.warning(
                        f"{self.engine_id}: no response from server"
                    )
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.close()
                    self.request_retries -= 1
                    break

                message_from_server = await socket.recv()
                if message_from_server == network_engine.HEARTBEAT:
                    logger.debug(
                        f"{self.engine_id} received heartbeat from server"
                    )
                    await socket.send(network_engine.HEARTBEAT)
                    continue

                logger.debug(f"{self.engine_id} received input from server")
                from_client = gabriel_pb2.FromClient()
                from_client.ParseFromString(message_from_server)
                input_frame = from_client.input_frame

                result = self.engine.handle(input_frame)
                result_proto = gabriel_pb2.Result()

                if not isinstance(result.status, gabriel_pb2.Status):
                    raise TypeError(
                        f"Return status not populated correctly by engine. "
                        f"Expected a value of type gabriel_pb2.Status, found "
                        f"{type(result.status)}"
                    )

                result_proto.status.CopyFrom(result.status)

                if result.status.code == gabriel_pb2.StatusCode.SUCCESS:
                    payload = result.payload

                    if payload is None:
                        error_msg = "Engine did not specify result payload"
                        logger.error(error_msg)
                        result.status.code = (
                            gabriel_pb2.StatusCode.ENGINE_ERROR
                        )
                        result.status.message = error_msg

                    if isinstance(payload, str):
                        result_proto.string_result = payload
                    elif isinstance(payload, bytes):
                        result_proto.bytes_result = payload
                    elif isinstance(payload, Any):
                        result_proto.any_result.CopyFrom(payload)
                    else:
                        error_msg = (
                            f"Engine produced unsupported result payload "
                            f"type: {type(result.payload)}"
                        )
                        logger.error(error_msg)
                        result.status.code = (
                            gabriel_pb2.StatusCode.ENGINE_ERROR
                        )
                        result.status.message = error_msg

                result_proto.target_engine_id = self.engine_id
                result_proto.frame_id = from_client.frame_id
                from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
                from_standalone_engine.result.CopyFrom(result_proto)

                logger.debug(f"{self.engine_id} sending result to server")
                await socket.send(from_standalone_engine.SerializeToString())
        self.done_event.set()

        logger.warning(
            f"{self.engine_id} ran out of retries. Abandoning server "
            f"connection."
        )

    async def stop(self):
        """Stops the engine runner."""
        self.running = False
        await self.done_event.wait()
