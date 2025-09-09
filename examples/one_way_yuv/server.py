import argparse
import cv2
import numpy as np

import common
import yuv_pb2
from gabriel_protocol import gabriel_pb2
from gabriel_server import local_engine
from gabriel_server import cognitive_engine


class DisplayEngine(cognitive_engine.Engine):
    def handle(self, input_frame):
        yuv = np.frombuffer(input_frame.payloads[0], dtype=np.uint8)

        to_server = cognitive_engine.unpack_extras(yuv_pb2.ToServer, input_frame)
        width = to_server.width
        height = to_server.height
        rotation = to_server.rotation

        yuv = np.reshape(yuv, ((height + (height // 2)), width))
        img = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV21)

        if rotation != 0:
            # np.rot90(img, 3) would correctly display an image rotated 90
            # degress from Android
            times_to_rotate = (360 - rotation) / 90
            img = np.rot90(img, times_to_rotate)

        cv2.imshow("Image from client", img)
        cv2.waitKey(1)

        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)
        return result_wrapper


def main():
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("source_name", nargs="?", default=common.DEFAULT_SOURCE_NAME)
    args = parser.parse_args()

    def engine_factory():
        return DisplayEngine()

    local_engine.run(
        engine_factory,
        args.source_name,
        input_queue_maxsize=60,
        port=common.WEBSOCKET_PORT,
        num_tokens=2,
    )


if __name__ == "__main__":
    main()
