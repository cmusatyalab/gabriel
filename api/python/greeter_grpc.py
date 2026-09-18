"""Hand-written gRPC bindings for the flatc-generated Greeter service.

flatc's Python gRPC generator has never worked - it fails with "Unable to
generate GRPC interface for Python" on every released flatbuffers version,
including current ones (see google/flatbuffers#8325) - so unlike
fbs/Greeting.py, this file is maintained by hand rather than generated.

It plugs FlatBuffers bytes into grpc's generic-handler API instead of
relying on protoc-style codegen, and targets the same wire route
("/fbs.Greeter/Greet") that the flatc-generated Go and C++ stubs use, so a
client or server written here interoperates with those.
"""

import flatbuffers
import grpc
from fbs.Greeting import Greeting

_SERVICE_NAME = "fbs.Greeter"
_METHOD_NAME = "Greet"
_FULL_METHOD = f"/{_SERVICE_NAME}/{_METHOD_NAME}"


def _serialize(builder: flatbuffers.Builder) -> bytes:
    return bytes(builder.Output())


def _deserialize(data: bytes) -> Greeting:
    return Greeting.GetRootAs(bytearray(data), 0)


class GreeterStub:
    """Client stub for the Greeter service."""

    def __init__(self, channel: grpc.Channel):
        """Construct the stub over an existing channel."""
        self.Greet = channel.unary_unary(
            _FULL_METHOD,
            request_serializer=_serialize,
            response_deserializer=_deserialize,
        )


class GreeterServicer:
    """Base class for Greeter service implementations."""

    def greet(
        self, request: Greeting, context: grpc.ServicerContext
    ) -> flatbuffers.Builder:
        """Handle a Greet RPC; override in a subclass."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Method not implemented!")
        raise NotImplementedError("Method not implemented!")


def add_greeter_servicer_to_server(
    servicer: GreeterServicer, server: grpc.Server
) -> None:
    """Register a GreeterServicer implementation with a grpc.Server."""
    rpc_method_handlers = {
        _METHOD_NAME: grpc.unary_unary_rpc_method_handler(
            servicer.greet,
            request_deserializer=_deserialize,
            response_serializer=_serialize,
        ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
        _SERVICE_NAME, rpc_method_handlers
    )
    server.add_generic_rpc_handlers((generic_handler,))
