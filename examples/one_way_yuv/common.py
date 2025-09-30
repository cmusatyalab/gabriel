"""Common configurations and utilities for one_way_yuv example."""

import logging

WEBSOCKET_PORT = 9099
DEFAULT_SOURCE_NAME = "camera_yuv"


def configure_logging():
    """Configures the logging."""
    logging.basicConfig(
        format="%(levelname)s: %(message)s", level=logging.INFO
    )
