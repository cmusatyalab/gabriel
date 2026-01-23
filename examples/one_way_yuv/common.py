"""Common configurations and utilities for one_way_yuv example."""

import logging

ZEROMQ_PORT = 9099
DEFAULT_ENGINE_ID = "camera_yuv"


def configure_logging():
    """Configures the logging."""
    logging.basicConfig(
        format="%(levelname)s: %(message)s", level=logging.INFO
    )
