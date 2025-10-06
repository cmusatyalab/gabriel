"""A Gabriel server that receives YUV frames and displays them."""

import argparse

import common
import cv2
import numpy as np
import yuv_pb2
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine, local_engine


class DisplayEngine(cognitive_engine.Engine):
    """A simple cognitive engine that displays the input frame."""

    def handle(self, input_frame):
        """Handles an input frame."""
        yuv = np.frombuffer(input_frame.payloads[0], dtype=np.uint8)

        to_server = cognitive_engine.unpack_extras(
            yuv_pb2.ToServer, input_frame
        )
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
    """Starts the Gabriel server."""
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "engine_name", nargs="?", default=common.DEFAULT_ENGINE_NAME
    )
    args = parser.parse_args()

    def engine_factory():
        return DisplayEngine()

    engine = local_engine.LocalEngine(
        engine_factory,
        input_queue_maxsize=60,
        port=common.ZEROMQ_PORT,
        num_tokens=2,
        engine_name=args.engine_name,
        use_zeromq=True,
    )

    engine.run()


if __name__ == "__main__":
    main()
