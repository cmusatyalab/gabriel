"""A Gabriel engine that displays the input frame."""

import common
import cv2
import numpy as np
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.cognitive_engine import Result
from gabriel_server.network_engine import engine_runner

SERVER_ADDRESS_FORMAT = "tcp://{}:{}"


class DisplayEngine(cognitive_engine.Engine):
    """A simple cognitive engine that displays the input frame."""

    def __init__(self, engine_name):
        """Initializes the display engine."""
        self._engine_name = engine_name

    def handle(self, input_frame):
        """Handles an input frame."""
        status = gabriel_pb2.Status()
        status.code = gabriel_pb2.StatusCode.SUCCESS

        return Result(status, "Hello from engine")
        np_data = np.frombuffer(input_frame.payloads[0], dtype=np.uint8)
        frame = cv2.imdecode(np_data, cv2.IMREAD_COLOR)
        cv2.imshow(f"Image from engine: {self._engine_name}", frame)
        cv2.waitKey(1)

        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)
        return result_wrapper


def main():
    """Starts the Gabriel engine."""
    common.configure_logging()
    args = common.parse_engine_name_server_host()
    engine = DisplayEngine(args.engine_name)

    server_address = SERVER_ADDRESS_FORMAT.format(
        args.server_host, common.SERVER_BACKEND_PORT
    )
    runner = engine_runner.EngineRunner(
        engine, args.engine_name, server_address
    )
    runner.run()


if __name__ == "__main__":
    main()
