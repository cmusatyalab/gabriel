from gabriel_protocol import gabriel_pb2
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.websocket_client import ProducerWrapper
from gabriel_client import push_source
import asyncio


def main():
    async def producer():
        # Do not use time.sleep. This would stop the event loop.
        await asyncio.sleep(1)

        return gabriel_pb2.InputFrame()

    producer_wrappers = [
        ProducerWrapper(producer=producer, source_name="empty")
    ]
    client = WebsocketClient(
        host="localhost",
        port=9099,
        producer_wrappers=producer_wrappers,
        consumer=push_source.consumer,
    )
    client.launch()


if __name__ == "__main__":
    main()
