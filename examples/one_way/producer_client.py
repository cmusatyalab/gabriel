import argparse
import cv2
import time
import numpy as np
import logging
from multiprocessing import Process
from gabriel_protocol import gabriel_pb2
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.websocket_client import ProducerWrapper


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


DEFAULT_SOURCE_NAME = 'oneway'


def consumer(result_wrapper):
    print('Got back {} results'.format(result_wrapper.results))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_name', nargs='?', default=DEFAULT_SOURCE_NAME)
    args = parser.parse_args()

    capture = cv2.VideoCapture(0)
    async def producer():
        _, frame = capture.read()
        _, jpeg_frame=cv2.imencode('.jpg', frame)
        input_frame = gabriel_pb2.InputFrame()
        input_frame.payload_type = gabriel_pb2.PayloadType.IMAGE
        input_frame.payloads.append(jpeg_frame.tostring())

        return input_frame

    producer_wrappers = [
        ProducerWrapper(producer=producer, source_name=args.source_name)
    ]
    client = WebsocketClient(
        'localhost', 9099, producer_wrappers, consumer)
    client.launch()


if __name__ == '__main__':
    main()
