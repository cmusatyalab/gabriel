import argparse
import cv2
import time
import numpy as np
import logging
from multiprocessing import Process
from gabriel_protocol import gabriel_pb2
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.websocket_client import ProducerWrapper
from gabriel_client.opencv_adapter import OpencvAdapter


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


DEFAULT_SOURCE_NAME = 'roundtrip'


def preprocess(frame):
    return frame


def produce_extras():
    return None


def consume_frame(frame, _):
    cv2.imshow('Image from server', frame)
    cv2.waitKey(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_name', nargs='?', default=DEFAULT_SOURCE_NAME)
    args = parser.parse_args()

    capture = cv2.VideoCapture(0)
    opencv_adapter = OpencvAdapter(
        preprocess, produce_extras, consume_frame, capture, args.source_name)

    client = WebsocketClient(
        'localhost', 9099, opencv_adapter.get_producer_wrappers(),
        opencv_adapter.consumer)
    client.launch()


if __name__ == '__main__':
    main()
