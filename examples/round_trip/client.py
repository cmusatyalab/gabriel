import argparse
import common
import cv2
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.opencv_adapter import OpencvAdapter


DEFAULT_SERVER_HOST = 'localhost'


def preprocess(frame):
    return frame


def produce_extras():
    return None


def consume_frame(frame, _):
    cv2.imshow('Image from server', frame)
    cv2.waitKey(1)


def main():
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'source_name', nargs='?', default=common.DEFAULT_SOURCE_NAME)
    parser.add_argument('server_host', nargs='?', default=DEFAULT_SERVER_HOST)
    args = parser.parse_args()

    capture = cv2.VideoCapture(0)
    opencv_adapter = OpencvAdapter(
        preprocess, produce_extras, consume_frame, capture, args.source_name)

    client = WebsocketClient(
        args.server_host, common.WEBSOCKET_PORT,
        opencv_adapter.get_producer_wrappers(), opencv_adapter.consumer)
    client.launch()


if __name__ == '__main__':
    main()
