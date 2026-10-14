"""Shows Gabriel's API in the fewest steps.

An app sends text to an engine that upper-cases it, and reads the result back.

    cd python && uv run python ../examples/python/hello.py

Everything runs in one thread, one step after another. In a real deployment
the server, the engine and the app are separate processes.
"""

import gabriel

FLOW = """
[[producers]]
name = "hello-app"
tokens = 1

[[engines]]
name = "hello-upper"
location = "local"

[[edges]]
from = "hello-app"
to = "hello-upper"
timeout = "1s"
"""
WAIT = 5.0  # seconds

# 1. A server, and an engine it serves.
with (
    gabriel.Server("hello-server") as server,
    gabriel.Engine("hello-upper", max_output_size=1024) as engine,
):
    server.add_engine("hello-upper")

    # 2. A producer for the app's inputs, and a subscription to what happens
    # to them.
    with (
        gabriel.Producer("hello-app", max_input_size=1024) as producer,
        gabriel.Subscription.producer("hello-app") as events,
        # 3. A client that sends the producer's inputs through the flow.
        gabriel.Client("hello-client", "hello-server") as client,
    ):
        client.add_producer("hello-app")
        client.add_flow("hello", FLOW)

        # 4. The app sends an input.
        producer.send("hello", b"hello, gabriel")

        # 5. The engine gets it, and reports its result.
        input = engine.next(WAIT)
        engine.stop(bytes(input.data).upper())

        # 6. The app reads the result.
        while (event := events.next(WAIT)).kind != gabriel.EventKind.RESULT:
            pass
        print(f"{event.engine} replied: {event.result.decode()}")
