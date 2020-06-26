import argparse
import cv2
import time
import logging
from multiprocessing import Process
from gabriel_protocol import gabriel_pb2
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.websocket_client import ProducerWrapper


DEFAULT_NUM_SOURCES = 1
ORG = (0, 120)
FONT_FACE = cv2.FONT_HERSHEY_PLAIN
FONT_SCALE = 10
COLOR = (255, 0, 0)


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


def consumer(result_wrapper):
    print('Got back {} results'.format(result_wrapper.results))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('num_sources', type=int, nargs='?',
                        default=DEFAULT_NUM_SOURCES)
    args = parser.parse_args()

    capture = cv2.VideoCapture(0)
    def gen_producer(n):
        text = 'client {}'.format(n)
        async def producer():
            _, frame = capture.read()
            cv2.putText(frame, text, ORG, FONT_FACE, FONT_SCALE, COLOR)
            _, jpeg_frame=cv2.imencode('.jpg', frame)
            input_frame = gabriel_pb2.InputFrame()
            input_frame.payload_type = gabriel_pb2.PayloadType.IMAGE
            input_frame.payloads.append(jpeg_frame.tostring())

            return input_frame
        return producer

    producer_wrappers = [
        ProducerWrapper(producer=gen_producer(i), source_name=str(i))
        for i in range(args.num_sources)
    ]
    client = WebsocketClient(
        'localhost', 9099, producer_wrappers, consumer)
    client.launch()


if __name__ == '__main__':
    main()
