"""A Gabriel server that handles empty messages."""

from gabriel_protocol import gabriel_pb2
from gabriel_server import cognitive_engine, local_engine


class EmptyEngine(cognitive_engine.Engine):
    """A simple cognitive engine that does nothing."""

    def handle(self, input_frame, client_info):
        """Handles an input frame."""
        status = gabriel_pb2.Status()
        status.code = gabriel_pb2.StatusCode.SUCCESS
        return cognitive_engine.Result(status, "")


def main():
    """Starts a Gabriel server that handles empty messages."""
    engine = local_engine.LocalEngine(
        engine_factory=lambda: EmptyEngine(),
        input_queue_maxsize=60,
        port=9099,
        num_tokens=2,
        engine_id="empty",
        use_zeromq=True,
    )
    engine.run()


if __name__ == "__main__":
    main()
