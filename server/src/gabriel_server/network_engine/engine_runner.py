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
        engine_name,
        server_address,
        all_responses_required=False,
        timeout=TEN_SECONDS,
        request_retries=REQUEST_RETRIES,
    ):
        self.engine = engine
        self.engine_name = engine_name
        self.server_address = server_address
        self.all_responses_required = all_responses_required
        self.timeout = timeout
        self.request_retries = request_retries
        self.running = True

    def run(self):
        context = zmq.Context()

        while self.request_retries > 0:
            socket = context.socket(zmq.REQ)
            socket.connect(self.server_address)
            from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
            from_standalone_engine.welcome.engine_name = self.engine_name
            from_standalone_engine.welcome.all_responses_required = (
                self.all_responses_required
            )
            socket.send(from_standalone_engine.SerializeToString())
            logger.info("Sent welcome message to server")

            while self.running:
                if socket.poll(self.timeout) == 0:
                    logger.warning(f"No response from server for {self.engine_name}")
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.close()
                    self.request_retries -= 1
                    break

                message_from_server = socket.recv()
                if message_from_server == network_engine.HEARTBEAT:
                    logger.info(f"{self.engine_name} received heartbeat from server")
                    socket.send(network_engine.HEARTBEAT)
                    continue

                logger.info(f"{self.engine_name} received input frame from server")

                input_frame = gabriel_pb2.InputFrame()
                input_frame.ParseFromString(message_from_server)
                result_wrapper = self.engine.handle(input_frame)
                result_wrapper.result_producer_name.value = self.engine_name

                from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
                from_standalone_engine.result_wrapper.CopyFrom(result_wrapper)

                socket.send(from_standalone_engine.SerializeToString())

        logger.warning("Ran out of retires. Abandoning server connection.")

    async def run_async(self):
        context = zmq.asyncio.Context()

        while self.request_retries > 0:
            socket = context.socket(zmq.REQ)
            socket.connect(self.server_address)
            from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
            from_standalone_engine.welcome.engine_name = self.engine_name
            from_standalone_engine.welcome.all_responses_required = (
                self.all_responses_required
            )
            await socket.send(from_standalone_engine.SerializeToString())
            logger.info("Sent welcome message to server")

            while self.running:
                if await socket.poll(self.timeout) == 0:
                    logger.warning("No response from server")
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.close()
                    self.request_retries -= 1
                    break

                message_from_server = await socket.recv()
                if message_from_server == network_engine.HEARTBEAT:
                    logger.info("Received heartbeat from server")
                    await socket.send(network_engine.HEARTBEAT)
                    continue

                input_frame = gabriel_pb2.InputFrame()
                input_frame.ParseFromString(message_from_server)
                result_wrapper = self.engine.handle(input_frame)
                result_wrapper.result_producer_name.value = self.engine_name

                from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
                from_standalone_engine.result_wrapper.CopyFrom(result_wrapper)
                await socket.send(from_standalone_engine.SerializeToString())

        logger.warning("Ran out of retires. Abandoning server connection.")

    def stop(self):
        self.running = False
