"""Common utilities for round trip examples."""

import logging

WEBSOCKET_PORT = 9099
DEFAULT_ENGINE_NAME = "Engine-0"


def configure_logging():
    """Configure logging to show the time, level and message."""
    logging.basicConfig(
        format="%(levelname)s: %(message)s", level=logging.INFO
    )
