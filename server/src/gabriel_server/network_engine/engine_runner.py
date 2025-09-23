"""
Engine runner that connects to the server and handles communication
between the cognitive engine and the server.
"""

import asyncio
import logging
import zmq
import zmq.asyncio
from gabriel_protocol import gabriel_pb2
from gabriel_server import network_engine


TEN_SECONDS = 10000
REQUEST_RETRIES = 3

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


class EngineRunner:
    def __init__(
        self,
        engine,
        engine_name: str,
        server_address: str,
        all_responses_required: bool = False,
        timeout: int = TEN_SECONDS,
        request_retries: int = REQUEST_RETRIES,
    ):
        """
        Initializes the engine runner.

        Args:
            engine:
                The cognitive engine instance to run, must have a handle()
                method.
            engine_name (str): The name of the engine.
            server_address (str): The address of the server to connect to.
            all_responses_required (bool): Whether all responses are required from the engine.
            timeout (int): The timeout in milliseconds to wait for a response from the server.
            request_retries (int): The number of times to retry connecting to the server.
        """
        self.engine = engine
        self.engine_name = engine_name
        self.server_address = server_address
        self.all_responses_required = all_responses_required
        self.timeout = timeout
        self.request_retries = request_retries
        self.running = True
        self.done_event = asyncio.Event()

    def run(self):
        """
        Connects to the server and starts listening to messages.
        """
        asyncio.run(self.run_async())

    async def run_async(self):
        """
        Connects to the server and starts listening to messages.
        """
        context = zmq.asyncio.Context()

        while self.running and self.request_retries > 0:
            socket = context.socket(zmq.REQ)
            socket.connect(self.server_address)
            from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
            from_standalone_engine.welcome.engine_name = self.engine_name
            from_standalone_engine.welcome.all_responses_required = (
                self.all_responses_required
            )
            await socket.send(from_standalone_engine.SerializeToString())
            logger.info(
                f"{self.engine_name} sent welcome message to server {self.server_address}"
            )

            while self.running:
                if await socket.poll(self.timeout) == 0:
                    logger.warning(
                        f"{self.engine_name}: no response from server"
                    )
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.close()
                    self.request_retries -= 1
                    break

                message_from_server = await socket.recv()
                if message_from_server == network_engine.HEARTBEAT:
                    logger.debug(
                        f"{self.engine_name} received heartbeat from server"
                    )
                    await socket.send(network_engine.HEARTBEAT)
                    continue

                logger.info(f"{self.engine_name} received input from server")
                input_frame = gabriel_pb2.InputFrame()
                input_frame.ParseFromString(message_from_server)
                result_wrapper = self.engine.handle(input_frame)

                logger.info(f"{self.engine_name} sending result to server")

                result_wrapper.result_producer_name.value = self.engine_name
                from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
                from_standalone_engine.result_wrapper.CopyFrom(result_wrapper)
                await socket.send(from_standalone_engine.SerializeToString())
        self.done_event.set()

        logger.warning(
            f"{self.engine_name} ran out of retries. Abandoning server connection."
        )

    async def stop(self):
        """
        Stops the engine runner.
        """
        self.running = False
        await self.done_event.wait()
