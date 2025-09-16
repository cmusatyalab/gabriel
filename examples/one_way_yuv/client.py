import argparse
import common
import cv2

import yuv_pb2
from gabriel_protocol import gabriel_pb2
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.websocket_client import ProducerWrapper
from gabriel_client import push_source


DEFAULT_SERVER_HOST = "localhost"
ROTATION = 0


class YuvConverter:
    def __init__(self, frame):
        self._frame = frame

    def convert_to_nv21(self):
        # As of version 4.5.0, OpenCV has no COLOR_BGR2YUV_NV21 converter.
        # I made this converter, which uses COLOR_BGR2YUV_I420 as a starting
        # point.
        # See here for details about l420 and NV21 https://wiki.videolan.org/YUV

        bgr_height, width, _ = self._frame.shape
        yuv = cv2.cvtColor(self._frame, cv2.COLOR_BGR2YUV_I420)
        yuv_height = yuv.shape[0]

        chrominance = []
        chrominance_height = yuv_height - bgr_height
        u_height = int(chrominance_height / 2)
        for y in range(u_height):
            for x in range(width):
                chrominance.append(yuv[bgr_height + u_height + y, x])
                chrominance.append(yuv[bgr_height + y, x])

        # Convert i420 representation to NV21
        chrominance = iter(chrominance)
        for y in range(chrominance_height):
            for x in range(width):
                yuv[bgr_height + y, x] = next(chrominance)

        return yuv


def main():
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_name", nargs="?", default=common.DEFAULT_SOURCE_NAME
    )
    parser.add_argument("server_host", nargs="?", default=DEFAULT_SERVER_HOST)
    args = parser.parse_args()

    capture = cv2.VideoCapture(0)

    async def producer():
        _, frame = capture.read()
        if frame is None:
            return None

        height, width, _ = frame.shape
        yuv_converter = YuvConverter(frame)
        yuv = yuv_converter.convert_to_nv21()

        input_frame = gabriel_pb2.InputFrame()
        input_frame.payload_type = gabriel_pb2.PayloadType.IMAGE
        input_frame.payloads.append(yuv.tobytes())

        to_server = yuv_pb2.ToServer()
        to_server.height = height
        to_server.width = width
        to_server.rotation = ROTATION

        input_frame.extras.Pack(to_server)

        return input_frame

    producer_wrappers = [
        ProducerWrapper(producer=producer, source_name=args.source_name)
    ]

    client = WebsocketClient(
        args.server_host,
        common.WEBSOCKET_PORT,
        producer_wrappers,
        push_source.consumer,
    )
    client.launch()


if __name__ == "__main__":
    main()
