import argparse
import common
from gabriel_protocol import gabriel_pb2
from gabriel_server import local_engine
from gabriel_server import cognitive_engine


class DisplayEngine(cognitive_engine.Engine):
    def handle(self, input_frame):
        status = gabriel_pb2.ResultWrapper.Status.SUCCESS
        result_wrapper = cognitive_engine.create_result_wrapper(status)

        result = gabriel_pb2.ResultWrapper.Result()
        result.payload_type = gabriel_pb2.PayloadType.IMAGE
        result.payload = input_frame.payloads[0]
        result_wrapper.results.append(result)

        return result_wrapper


def main():
    common.configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_name", nargs="?", default=common.DEFAULT_SOURCE_NAME
    )
    args = parser.parse_args()

    def engine_factory():
        return DisplayEngine()

    local_engine.run(
        engine_factory,
        args.source_name,
        input_queue_maxsize=60,
        port=common.WEBSOCKET_PORT,
        num_tokens=2,
    )


if __name__ == "__main__":
    main()
