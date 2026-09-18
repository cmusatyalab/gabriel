"""Verify the hand-written Greeter gRPC bindings work end to end.

Starts a Greeter gRPC server and calls it through GreeterStub (mirrors
cmd/roundtrip-example's Go gRPC demo). Run from api/python with:

    uv run --with grpcio --with flatbuffers python3 greeter_grpc_test.py
"""

from concurrent import futures

import flatbuffers
import grpc
from fbs import Greeting as GreetingModule
from fbs.Greeting import Greeting
from greeter_grpc import (
    GreeterServicer,
    GreeterStub,
    add_greeter_servicer_to_server,
)


def _build_greeting(
    builder: flatbuffers.Builder, id_: int, message: str
) -> None:
    msg = builder.CreateString(message)
    GreetingModule.GreetingStart(builder)
    GreetingModule.GreetingAddId(builder, id_)
    GreetingModule.GreetingAddMessage(builder, msg)
    builder.Finish(GreetingModule.GreetingEnd(builder))


class _Greeter(GreeterServicer):
    def greet(
        self, request: Greeting, context: grpc.ServicerContext
    ) -> flatbuffers.Builder:
        b = flatbuffers.Builder(0)
        greeting = "hello, " + request.Message().decode()
        _build_greeting(b, request.Id() + 1, greeting)
        return b


def main() -> None:
    """Run the Greeter server/client round trip and check the result."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    add_greeter_servicer_to_server(_Greeter(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = GreeterStub(channel)

            request = flatbuffers.Builder(0)
            _build_greeting(request, 41, "world")

            response = stub.Greet(request)
            assert response.Id() == 42, response.Id()
            assert response.Message() == b"hello, world", response.Message()
            print(f"grpc: id={response.Id()} message={response.Message()!r}")
    finally:
        server.stop(None)


if __name__ == "__main__":
    main()
