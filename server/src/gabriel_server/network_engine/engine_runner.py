"""Engine runner that connects to the server.

Handles communication between the cognitive engine and the server.
"""

import asyncio
import logging

import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2
from google.protobuf.any_pb2 import Any

from gabriel_server import cognitive_engine, network_engine

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

        try:
            while self.running and self.request_retries > 0:
                socket = context.socket(zmq.REQ)
                try:
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.connect(self.server_address)
                    await self.engine_loop(socket)
                except Exception as e:
                    logger.error(e)
                    raise
                finally:
                    socket.close()
        finally:
            context.term()

        self.done_event.set()

        logger.warning(
            f"{self.engine_id} ran out of retries. Abandoning server "
            f"connection."
        )

    async def engine_loop(self, socket):
        """Listen for messages from the server."""
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
                logger.warning(f"{self.engine_id}: no response from server")
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

            # Send the frame to the engine handler.
            result = self.engine.handle(input_frame)

            result_proto = gabriel_pb2.Result()
            result_proto.target_engine_id = self.engine_id
            result_proto.frame_id = from_client.frame_id

            if not isinstance(result, cognitive_engine.Result):
                error_msg = (
                    f"Incorrect type returned by engine. "
                    f"Expected a value of type "
                    f"cognitive_engine.Result, found {type(result)}"
                )
                logger.error(error_msg)
                result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
                result_proto.status.message = error_msg
                await socket.send(create_engine_result_payload(result_proto))
                return

            if not isinstance(result.status, gabriel_pb2.Status):
                error_msg = (
                    f"Return status not populated correctly by "
                    f"engine. Expected a value of type "
                    f"gabriel_pb2.Status, found {type(result.status)}"
                )
                logger.error(error_msg)
                result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
                result_proto.status.message = error_msg
                await socket.send(create_engine_result_payload(result_proto))
                return
            result_proto.status.CopyFrom(result.status)

            if result.status.code != gabriel_pb2.StatusCode.SUCCESS:
                logger.debug(f"{self.engine_id} sending error to server")
                await socket.send(create_engine_result_payload(result_proto))
                return

            payload = result.payload
            if payload is None:
                error_msg = "Engine did not specify result payload"
                logger.error(error_msg)
                result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
                result_proto.status.message = error_msg
                await socket.send(create_engine_result_payload(result_proto))
                return

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
                result_proto.status.code = gabriel_pb2.StatusCode.ENGINE_ERROR
                result_proto.status.message = error_msg
                await socket.send(create_engine_result_payload(result_proto))
                return

            # result_proto.status.code = gabriel_pb2.StatusCode.SUCCESS
            logger.debug(f"{self.engine_id} sending result to server")
            await socket.send(create_engine_result_payload(result_proto))

    async def stop(self):
        """Stops the engine runner."""
        self.running = False
        await self.done_event.wait()


def create_engine_result_payload(result_proto):
    """Create an engine result payload to send to the server."""
    from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
    from_standalone_engine.result.CopyFrom(result_proto)
    return from_standalone_engine.SerializeToString()
