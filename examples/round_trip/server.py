"""A simple Gabriel server that displays the input frame."""

import argparse

import common
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine, local_engine


class DisplayEngine(cognitive_engine.Engine):
    """A simple cognitive engine that displays the input frame."""

    def handle(self, input_frame):
        """Handles an input frame."""
        status = gabriel_pb2.Status()
        status.code = gabriel_pb2.StatusCode.SUCCESS
        return cognitive_engine.Result(
            status=status, payload=input_frame.byte_payload
        )


def main():
    """Starts the Gabriel server."""
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "engine_id", nargs="?", default=common.DEFAULT_ENGINE_ID
    )
    args = parser.parse_args()

    def engine_factory():
        return DisplayEngine()

    engine = local_engine.LocalEngine(
        engine_factory,
        input_queue_maxsize=60,
        port=common.ZEROMQ_PORT,
        num_tokens=2,
        engine_id=args.engine_id,
        use_zeromq=True,
    )

    engine.run()


if __name__ == "__main__":
    main()
