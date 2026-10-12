"""The old Gabriel's processes.

They use the gabriel-server and gabriel-client packages, over one of these
transports:

- websocket: the app reaches the server by WebSocket over TCP, and the engine
  reaches it by gRPC over TCP. This is the default setup. The WebSocket
  library compresses each message, which the client cannot turn off.
- websocket-uncompressed: as websocket, with compression turned off.
- tcp: as websocket, but the app uses ZeroMQ.
- ipc: the app uses ZeroMQ, and both it and the engine reach the server over
  Unix sockets.

    python old_gabriel.py server|engine|app --transport ipc ...

The runner (compare.py) starts them.
"""

import asyncio
import contextlib
import functools
import itertools
import logging
import os
import time

import common
import grpc
import websockets
from gabriel_client.gabriel_client import InputProducer
from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.zeromq_client import ZeroMQClient
from gabriel_protocol.v1 import gabriel_pb2
from gabriel_server import cognitive_engine
from gabriel_server.network_engine.engine_runner import EngineRunner
from gabriel_server.network_engine.server_runner import (
    ServerRunner,
    Transport,
)

WEBSOCKET_TRANSPORTS = ["websocket", "websocket-uncompressed"]
TRANSPORTS = [*WEBSOCKET_TRANSPORTS, "tcp", "ipc"]
ENGINE = "compare-engine"
PRODUCER = "compare-app"
CLIENT_PORT = 9099
ENGINE_PORT = 9100
# Lets the operating system pick a free port for the server's metrics.
ANY_PORT = 0
# Inputs the server queues for the engine. With one token, one is enough.
INPUT_QUEUE_SIZE = 1
# Above the largest frame, so the server takes every frame.
MAX_MESSAGE_SIZE = 64 * 1024 * 1024
# How often the server process checks whether its server has started.
START_POLL = 0.01  # seconds
# What the engine runner logs once it has registered with the server.
REGISTERED_MESSAGE = "sent register message"


def ipc_path(args, name: str) -> str:
    """Gives the path of the Unix socket `name`."""
    return os.path.join(args.ipc_dir, f"{name}.sock")


def engine_address(args) -> str:
    """Gives the address the engine reaches the server on."""
    if args.transport == "ipc":
        return f"unix:{ipc_path(args, 'engine')}"
    return f"localhost:{ENGINE_PORT}"


async def server(args) -> None:
    """Runs the server until stopped."""
    ipc = args.transport == "ipc"
    runner = ServerRunner(
        client_endpoint=ipc_path(args, "client") if ipc else CLIENT_PORT,
        engine_endpoint=ipc_path(args, "engine") if ipc else ENGINE_PORT,
        num_tokens=common.TOKENS,
        input_queue_maxsize=INPUT_QUEUE_SIZE,
        message_max_size=MAX_MESSAGE_SIZE,
        client_transport=(
            Transport.WEBSOCKET
            if args.transport in WEBSOCKET_TRANSPORTS
            else Transport.ZEROMQ
        ),
        prometheus_port=ANY_PORT,
        use_client_ipc=ipc,
        use_engine_ipc=ipc,
    )
    running = asyncio.create_task(runner.run_async())
    # The runner makes its server only once it runs.
    while not hasattr(runner, "server"):
        await asyncio.sleep(START_POLL)
    await runner.server.wait_for_start()
    common.say(common.READY)
    await running


class NullEngine(cognitive_engine.Engine):
    """An engine that answers each frame at once."""

    def handle(self, input_frame, client_info):
        """Answers the frame."""
        return cognitive_engine.Result(
            gabriel_pb2.Status(code=gabriel_pb2.StatusCode.SUCCESS),
            common.RESULT,
        )


class _ReadyOnRegister(logging.Handler):
    """Tells the runner the engine is ready once it has registered.

    The engine runner says so only in its log.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if REGISTERED_MESSAGE in record.getMessage():
            common.say(common.READY)


def allow_large_frames() -> None:
    """Lets the engine receive frames over 4 MB.

    gRPC refuses to receive messages over 4 MB unless told otherwise, and
    EngineRunner does not tell it, so no 1080p or 4K frame would reach the
    engine. This raises the limit on the engine's channel to the server.
    """
    insecure_channel = grpc.aio.insecure_channel

    def unlimited(target, options=(), *args, **kwargs):
        options = [*options, ("grpc.max_receive_message_length", -1)]
        return insecure_channel(target, options, *args, **kwargs)

    grpc.aio.insecure_channel = unlimited


async def engine(args) -> None:
    """Runs the engine until stopped."""
    allow_large_frames()
    log = logging.getLogger(EngineRunner.__module__)
    log.setLevel(logging.INFO)
    log.addHandler(_ReadyOnRegister())
    await EngineRunner(NullEngine(), ENGINE, engine_address(args)).run_async()


def client_endpoint(args) -> str:
    """Gives the address the app reaches the server on."""
    if args.transport in WEBSOCKET_TRANSPORTS:
        return f"ws://localhost:{CLIENT_PORT}"
    if args.transport == "tcp":
        return f"tcp://localhost:{CLIENT_PORT}"
    return f"ipc://{ipc_path(args, 'client')}"


async def app(args, recorder: common.Recorder) -> None:
    """Sends frames as the token allows, so one is in flight at a time."""
    frame = common.make_frame(args.resolution).tobytes()
    # When each frame was sent, by frame id. The client numbers frames from
    # one, in the order the producer makes them.
    sent: dict[int, int] = {}
    frame_ids = itertools.count(1)
    done = asyncio.Event()

    async def produce() -> gabriel_pb2.InputFrame:
        sent[next(frame_ids)] = time.perf_counter_ns()
        return gabriel_pb2.InputFrame(
            payload_type=gabriel_pb2.PayloadType.IMAGE, byte_payload=frame
        )

    def consume(result: gabriel_pb2.Result) -> None:
        recorder.record(time.perf_counter_ns() - sent.pop(result.frame_id))
        if recorder.done:
            done.set()

    if args.transport == "websocket-uncompressed":
        websockets.connect = functools.partial(
            websockets.connect, compression=None
        )
    producer = InputProducer(produce, [ENGINE], PRODUCER)
    client_class = (
        WebsocketClient
        if args.transport in WEBSOCKET_TRANSPORTS
        else ZeroMQClient
    )
    client = client_class(client_endpoint(args), [producer], consume)
    running = asyncio.create_task(client.launch_async())
    finished = asyncio.create_task(done.wait())
    await asyncio.wait(
        [running, finished], return_when=asyncio.FIRST_COMPLETED
    )
    running.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        # Raises what stopped the client, if it stopped early.
        await running


def main() -> None:
    """Runs the role the runner asks for."""
    args = common.role_arguments(TRANSPORTS)
    common.stop_on_sigterm()
    # Prints the old Gabriel's warnings and errors, but not the INFO lines
    # the engine's logger is raised to.
    problems = logging.StreamHandler()
    problems.setLevel(logging.WARNING)
    logging.basicConfig(handlers=[problems])
    roles = {
        "server": server,
        "engine": engine,
        "app": lambda args: app(
            args, common.Recorder(args.warmup, args.frames)
        ),
    }
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(roles[args.role](args))


if __name__ == "__main__":
    main()
