"""A simple Gabriel server that displays the input frame."""

import argparse

import common
from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine, local_engine


class DisplayEngine(cognitive_engine.Engine):
    """A simple cognitive engine that displays the input frame."""

    def handle(self, input_frame):
        """Handles an input frame."""
        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)

        result = gabriel_pb2.ResultWrapper.Result()
        result.payload_type = gabriel_pb2.PayloadType.IMAGE
        result.payload = input_frame.payloads[0]
        result_wrapper.results.append(result)

        return result_wrapper


def main():
    """Starts the Gabriel server."""
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "engine_name", nargs="?", default=common.DEFAULT_ENGINE_NAME
    )
    args = parser.parse_args()

    def engine_factory():
        return DisplayEngine()

    engine = local_engine.LocalEngine(
        engine_factory,
        input_queue_maxsize=60,
        port=common.ZEROMQ_PORT,
        num_tokens=2,
        engine_name=args.engine_name,
        use_zeromq=True,
    )

    engine.run()


if __name__ == "__main__":
    main()
