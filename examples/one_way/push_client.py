"""A Gabriel client that captures video from the webcam."""

import multiprocessing
import time

import common
import cv2
from gabriel_client import push_source
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2


def send_frames(source):
    """Sends frames captured from the webcam to the Gabriel server."""
    capture = cv2.VideoCapture(0)
    while True:
        _, frame = capture.read()
        _, jpeg_frame = cv2.imencode(".jpg", frame)
        input_frame = gabriel_pb2.InputFrame()
        input_frame.payload_type = gabriel_pb2.PayloadType.IMAGE
        input_frame.byte_payload = jpeg_frame.tobytes()

        source.send(input_frame)
        time.sleep(0.1)


def main():
    """Starts the Gabriel client."""
    common.configure_logging()
    args = common.parse_engine_id_server_host()
    source = push_source.Source(args.engine_id, [common.DEFAULT_ENGINE_ID])
    p = multiprocessing.Process(target=send_frames, args=(source,))
    p.start()
    input_producers = [source.get_input_producer()]
    client = ZeroMQClient(
        f"tcp://{args.server_host}:{common.SERVER_FRONTEND_PORT}",
        input_producers,
        push_source.consumer,
    )
    client.launch()
    p.terminate()


if __name__ == "__main__":
    main()
