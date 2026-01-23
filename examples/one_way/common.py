"""Common utilities for one-way example."""

import argparse
import logging

DEFAULT_ENGINE_ID = "0"
DEFAULT_SERVER_HOST = "localhost"
SERVER_FRONTEND_PORT = 9099
SERVER_BACKEND_PORT = 5555


def configure_logging():
    """Configures the logging."""
    logging.basicConfig(
        format="%(levelname)s: %(message)s", level=logging.INFO
    )


def parse_engine_id_server_host():
    """Parses command line arguments for engine name and server host."""
    parser = argparse.ArgumentParser()
    parser.add_argument("engine_id", nargs="?", default=DEFAULT_ENGINE_ID)
    parser.add_argument("server_host", nargs="?", default=DEFAULT_SERVER_HOST)
    return parser.parse_args()
