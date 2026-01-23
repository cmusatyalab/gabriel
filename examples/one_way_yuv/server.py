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
        to_server = yuv_pb2.ToServer()
        input_frame.any_payload.Unpack(to_server)

        yuv = np.frombuffer(to_server.image, dtype=np.uint8)
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

        status = gabriel_pb2.Status()
        status.code = gabriel_pb2.StatusCode.SUCCESS
        return cognitive_engine.Result(status=status, payload="")


def main():
    """Starts the Gabriel server."""
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "engine_id", nargs="?", default=common.DEFAULT_ENGINE_ID
    )
    args = parser.parse_args()

    def engine_factory():
        return DisplayEngine()

    engine = local_engine.LocalEngine(
        engine_factory,
        input_queue_maxsize=60,
        port=common.ZEROMQ_PORT,
        num_tokens=2,
        engine_id=args.engine_id,
        use_zeromq=True,
    )

    engine.run()


if __name__ == "__main__":
    main()
