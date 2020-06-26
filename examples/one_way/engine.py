import argparse
import cv2
import time
import logging
from gabriel_protocol import gabriel_pb2
# from gabriel_server.network_engine import engine_runner
from gabriel_server import local_engine
from gabriel_server import cognitive_engine
import numpy as np


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


DEFAULT_SOURCE_NAME = 'oneway'


class DisplayEngine(cognitive_engine.Engine):
    def handle(self, input_frame):
        np_data = np.fromstring(input_frame.payloads[0], dtype=np.uint8)
        frame = cv2.imdecode(np_data, cv2.IMREAD_COLOR)
        cv2.imshow("Image sent to server", frame)
        cv2.waitKey(1)

        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)
        return result_wrapper


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_name', nargs='?', default=DEFAULT_SOURCE_NAME)
    args = parser.parse_args()
    def engine_factory():
        return DisplayEngine()

    local_engine.run(engine_factory, args.source_name,
                     input_queue_maxsize=60, port=9099, num_tokens=2)


if __name__ == '__main__':
    main()
