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
    """Starts a Gabriel server that handles empty messages."""
    engine = local_engine.LocalEngine(
        engine_factory=lambda: EmptyEngine(),
        input_queue_maxsize=60,
        port=9099,
        num_tokens=2,
        engine_name="empty",
        use_zeromq=True,
    )
    engine.run()


if __name__ == "__main__":
    main()
