"""Common utilities for one-way example."""

import argparse
import logging

DEFAULT_SOURCE_NAME = "0"
DEFAULT_SERVER_HOST = "localhost"
WEBSOCKET_PORT = 9099
ZMQ_PORT = 5555


def configure_logging():
    """Configures the logging."""
    logging.basicConfig(
        format="%(levelname)s: %(message)s", level=logging.INFO
    )


def parse_source_name_server_host():
    """Parses command line arguments for source name and server host."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source_name", nargs="?", default=DEFAULT_SOURCE_NAME)
    parser.add_argument("server_host", nargs="?", default=DEFAULT_SERVER_HOST)
    return parser.parse_args()
