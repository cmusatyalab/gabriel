import common
import cv2
import time
import logging
from gabriel_protocol import gabriel_pb2
from gabriel_server.network_engine import engine_runner
from gabriel_server import cognitive_engine
import numpy as np


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


class DisplayEngine(cognitive_engine.Engine):
    def __init__(self, source_name):
        self._source_name = source_name

    def handle(self, input_frame):
        np_data = np.fromstring(input_frame.payloads[0], dtype=np.uint8)
        frame = cv2.imdecode(np_data, cv2.IMREAD_COLOR)
        cv2.imshow("Image from source: {}".format(self._source_name), frame)
        cv2.waitKey(1)

        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)
        return result_wrapper


def main():
    source_name = common.parse_source_name()
    engine = DisplayEngine(source_name)

    engine_runner.run(engine, source_name,
                      server_address='tcp://localhost:5555')


if __name__ == '__main__':
    main()
