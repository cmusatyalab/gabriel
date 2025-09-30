"""A Gabriel server that handles empty messages."""

from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine, local_engine


class EmptyEngine(cognitive_engine.Engine):
    """A simple cognitive engine that does nothing."""

    def handle(self, input_frame):
        """Handles an input frame."""
        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)
        return result_wrapper


def main():
    """Starts the Gabriel server."""
    local_engine.run(
        engine_factory=lambda: EmptyEngine(),
        source_name="empty",
        input_queue_maxsize=60,
        port=9099,
        num_tokens=2,
    )


if __name__ == "__main__":
    main()
