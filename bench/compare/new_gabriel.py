"""The new Gabriel's processes.

Frames go from the app to the engine through shared memory.

    python new_gabriel.py server|engine|app --resolution 720p ...

The runner (compare.py) starts them.
"""

import contextlib
import signal
import time

import common
import gabriel

SERVER = "compare-server"
ENGINE = "compare-engine"
PRODUCER = "compare-app"
CLIENT = "compare-client"
FLOW = "compare"
FLOW_SPEC = f"""
[[producers]]
name = "{PRODUCER}"
tokens = {common.TOKENS}

[[engines]]
name = "{ENGINE}"
location = "local"

[[edges]]
from = "{PRODUCER}"
to = "{ENGINE}"
timeout = "{common.WAIT}s"
"""


def server() -> None:
    """Serves the engine until stopped."""
    with gabriel.Server(SERVER) as server:
        server.add_engine(ENGINE)
        common.say(common.READY)
        signal.pause()


def engine() -> None:
    """Answers each frame at once, until stopped."""
    with gabriel.Engine(ENGINE, max_output_size=common.RESULT_SIZE) as engine:
        common.say(common.READY)
        for _ in engine:
            engine.stop(common.RESULT)


def app(resolution: str, recorder: common.Recorder) -> None:
    """Sends a frame, waits for its result, and sends the next."""
    frame = common.make_frame(resolution)
    with (
        gabriel.Producer(PRODUCER, max_input_size=frame.nbytes) as producer,
        gabriel.Subscription(PRODUCER) as events,
        gabriel.Client(CLIENT, SERVER) as client,
    ):
        client.add_producer(PRODUCER)
        client.add_flow(FLOW, FLOW_SPEC)
        while not recorder.done:
            sent = time.perf_counter_ns()
            producer.send(FLOW, frame)
            wait_for_result(events)
            recorder.record(time.perf_counter_ns() - sent)


def wait_for_result(events: gabriel.Subscription) -> None:
    """Waits for the next result, skipping other events."""
    while True:
        event = events.next(common.WAIT)
        if event is None:
            raise TimeoutError("no result came")
        if event.kind == gabriel.EventKind.RESULT:
            return


def main() -> None:
    """Runs the role the runner asks for."""
    args = common.role_arguments()
    common.stop_on_sigterm()
    with contextlib.suppress(KeyboardInterrupt):
        if args.role == "server":
            server()
        elif args.role == "engine":
            engine()
        else:
            app(args.resolution, common.Recorder(args.warmup, args.frames))


if __name__ == "__main__":
    main()
