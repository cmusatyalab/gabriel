"""A Gabriel server that receives YUV frames and displays them."""

import common
from gabriel_server.network_engine import server_runner

ZMQ_ADDRESS_FORMAT = "tcp://*:{}"


def main():
    """Starts the Gabriel server."""
    common.configure_logging()
    runner = server_runner.ServerRunner(
        common.SERVER_FRONTEND_PORT,
        f"tcp://{common.DEFAULT_SERVER_HOST}:{common.SERVER_BACKEND_PORT}",
        num_tokens=2,
        input_queue_maxsize=60,
    )
    runner.run()


if __name__ == "__main__":
    main()
