"""Example client that captures video and sends it to the server."""

import argparse

import common
import cv2
from gabriel_client.opencv_adapter import OpencvAdapter
from gabriel_client.zeromq_client import ZeroMQClient

DEFAULT_SERVER_HOST = "localhost"


def preprocess(frame):
    """Preprocess the frame before sending to server."""
    return frame


def consume_frame(frame):
    """Consume the frame received from server."""
    cv2.imshow("Image from server", frame)
    cv2.waitKey(1)


def main():
    """Run the example client."""
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "engine_id", nargs="?", default=common.DEFAULT_ENGINE_ID
    )
    parser.add_argument("server_host", nargs="?", default=DEFAULT_SERVER_HOST)
    args = parser.parse_args()

    capture = cv2.VideoCapture(0)
    opencv_adapter = OpencvAdapter(
        preprocess, consume_frame, capture, args.engine_id
    )

    client = ZeroMQClient(
        f"tcp://{args.server_host}:{common.ZEROMQ_PORT}",
        opencv_adapter.get_producer_wrappers(),
        opencv_adapter.consumer,
    )
    client.launch()


if __name__ == "__main__":
    main()
