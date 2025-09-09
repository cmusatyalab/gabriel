import logging


WEBSOCKET_PORT = 9099
DEFAULT_SOURCE_NAME = "camera_yuv"


def configure_logging():
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
