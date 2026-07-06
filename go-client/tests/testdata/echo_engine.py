"""Minimal cognitive engine used by the Go client's integration tests.

Started as a subprocess by tests/main_test.go, this connects to a Gabriel
server's ZeroMQ engine backend and echoes a fixed response for every input
frame it receives.
"""

import argparse
import logging

from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.cognitive_engine import Result
from gabriel_server.network_engine import engine_runner

logging.basicConfig(level=logging.INFO)


class EchoEngine(cognitive_engine.Engine):
    """A cognitive engine that returns a fixed response for every frame."""

    def __init__(self, engine_id):
        """Initializes the echo engine."""
        self._engine_id = engine_id

    def handle(self, input_frame):
        """Handles an input frame by returning a fixed success result."""
        status = gabriel_pb2.Status()
        status.code = gabriel_pb2.StatusCode.SUCCESS
        return Result(status, "hello")


def main():
    """Starts the echo engine."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-id", default="0")
    parser.add_argument("--server-address", default="tcp://localhost:5555")
    args = parser.parse_args()

    engine = EchoEngine(args.engine_id)
    runner = engine_runner.EngineRunner(
        engine, args.engine_id, args.server_address
    )
    runner.run()


if __name__ == "__main__":
    main()
