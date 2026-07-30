"""A Gabriel client that sends empty messages."""

import asyncio

from gabriel_client.gabriel_client import InputProducer
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol import gabriel_pb2


def consumer(result):
    """Consumes a result."""
    print(
        "Received a result with status:",
        gabriel_pb2.StatusCode.Name(result.status.code),
    )


def main():
    """Starts a Gabriel client that sends empty messages."""

    async def producer():
        # Do not use time.sleep. This would stop the event loop.
        await asyncio.sleep(1)

        return gabriel_pb2.InputFrame()

    input_producers = [
        InputProducer(
            producer=producer,
            producer_name="empty",
            target_engine_ids=["empty"],
        )
    ]
    client = ZeroMQClient(
        server_endpoint="tcp://localhost:9099",
        input_producers=input_producers,
        consumer=consumer,
    )
    client.launch()


if __name__ == "__main__":
    main()
