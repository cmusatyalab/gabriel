import common
import cv2
import logging
import multiprocessing
import time
from multiprocessing import Process
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client import push_source
from gabriel_protocol import gabriel_pb2


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


def send_frames(source):
    capture = cv2.VideoCapture(0)
    while True:
        _, frame = capture.read()
        _, jpeg_frame=cv2.imencode('.jpg', frame)
        input_frame = gabriel_pb2.InputFrame()
        input_frame.payload_type = gabriel_pb2.PayloadType.IMAGE
        input_frame.payloads.append(jpeg_frame.tostring())

        source.send(input_frame)
        time.sleep(0.1)


def main():
    args = common.parse_source_name_server_host()
    source = push_source.Source(args.source_name)
    p = multiprocessing.Process(target=send_frames, args=(source,))
    p.start()
    producer_wrappers = [source.get_producer_wrapper()]
    client = WebsocketClient(args.server_host, common.WEBSOCKET_PORT,
                             producer_wrappers, push_source.consumer)
    client.launch()
    p.terminate()


if __name__ == '__main__':
    main()
