"""Common utilities for round trip examples."""

import logging

ZEROMQ_PORT = 9099
DEFAULT_ENGINE_ID = "Engine-0"


def configure_logging():
    """Configure logging to show the time, level and message."""
    logging.basicConfig(
        format="%(levelname)s: %(message)s", level=logging.INFO
    )
