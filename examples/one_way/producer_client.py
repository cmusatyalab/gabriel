"""A Gabriel client that captures video from the webcam."""

import argparse

import common
import cv2
from gabriel_client import push_source
from gabriel_client.gabriel_client import InputProducer
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2

DEFAULT_NUM_SOURCES = 1
ORG = (0, 120)
FONT_FACE = cv2.FONT_HERSHEY_PLAIN
FONT_SCALE = 10
COLOR = (255, 0, 0)


def main():
    """Starts the Gabriel client."""
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "num_sources", type=int, nargs="?", default=DEFAULT_NUM_SOURCES
    )
    parser.add_argument(
        "server_host", nargs="?", default=common.DEFAULT_SERVER_HOST
    )
    args = parser.parse_args()

    capture = cv2.VideoCapture(0)

    def gen_producer(n):
        text = f"client {n}"

        async def producer():
            _, frame = capture.read()
            cv2.putText(frame, text, ORG, FONT_FACE, FONT_SCALE, COLOR)
            _, jpeg_frame = cv2.imencode(".jpg", frame)
            input_frame = gabriel_pb2.InputFrame()
            input_frame.payload_type = gabriel_pb2.PayloadType.IMAGE
            input_frame.payloads.append(jpeg_frame.tobytes())

            return input_frame

        return producer

    input_producers = [
        InputProducer(
            producer=gen_producer(i),
            target_engine_ids=[common.DEFAULT_ENGINE_NAME],
        )
        for i in range(args.num_sources)
    ]
    client = ZeroMQClient(
        f"tcp://{args.server_host}:{common.SERVER_FRONTEND_PORT}",
        input_producers,
        push_source.consumer,
    )
    client.launch()


if __name__ == "__main__":
    main()
