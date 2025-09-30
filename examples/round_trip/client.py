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


def produce_extras():
    """Produce extras to send to server."""
    return None


def consume_frame(frame, _):
    """Consume the frame received from server."""
    cv2.imshow("Image from server", frame)
    cv2.waitKey(1)


def main():
    """Run the example client."""
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "engine_name", nargs="?", default=common.DEFAULT_ENGINE_NAME
    )
    parser.add_argument("server_host", nargs="?", default=DEFAULT_SERVER_HOST)
    args = parser.parse_args()

    capture = cv2.VideoCapture(0)
    opencv_adapter = OpencvAdapter(
        preprocess, produce_extras, consume_frame, capture, args.engine_name
    )

    client = ZeroMQClient(
        f"tcp://{args.server_host}:{common.WEBSOCKET_PORT}",
        opencv_adapter.get_producer_wrappers(),
        opencv_adapter.consumer,
    )
    client.launch()


if __name__ == "__main__":
    main()
