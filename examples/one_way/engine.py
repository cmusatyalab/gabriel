import common
import cv2
from gabriel_protocol import gabriel_pb2
from gabriel_server.network_engine import engine_runner
from gabriel_server import cognitive_engine
import numpy as np


SERVER_ADDRESS_FORMAT = "tcp://{}:{}"


class DisplayEngine(cognitive_engine.Engine):
    def __init__(self, source_name):
        self._source_name = source_name

    def handle(self, input_frame):
        np_data = np.frombuffer(input_frame.payloads[0], dtype=np.uint8)
        frame = cv2.imdecode(np_data, cv2.IMREAD_COLOR)
        cv2.imshow("Image from source: {}".format(self._source_name), frame)
        cv2.waitKey(1)

        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)
        return result_wrapper


def main():
    common.configure_logging()
    args = common.parse_source_name_server_host()
    engine = DisplayEngine(args.source_name)

    server_address = SERVER_ADDRESS_FORMAT.format(
        args.server_host, common.ZMQ_PORT
    )
    engine_runner.run(engine, args.source_name, server_address)


if __name__ == "__main__":
    main()
