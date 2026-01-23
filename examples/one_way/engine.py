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

    def __init__(self, engine_id):
        """Initializes the display engine."""
        self._engine_id = engine_id

    def handle(self, input_frame):
        """Handles an input frame."""
        status = gabriel_pb2.Status()
        status.code = gabriel_pb2.StatusCode.SUCCESS

        np_data = np.frombuffer(input_frame.byte_payload, dtype=np.uint8)
        frame = cv2.imdecode(np_data, cv2.IMREAD_COLOR)
        cv2.imshow(f"Image from engine: {self._engine_id}", frame)
        cv2.waitKey(1)

        return Result(status, "Hello from engine")


def main():
    """Starts the Gabriel engine."""
    common.configure_logging()
    args = common.parse_engine_id_server_host()
    engine = DisplayEngine(args.engine_id)

    server_address = SERVER_ADDRESS_FORMAT.format(
        args.server_host, common.SERVER_BACKEND_PORT
    )
    runner = engine_runner.EngineRunner(engine, args.engine_id, server_address)
    runner.run()


if __name__ == "__main__":
    main()
