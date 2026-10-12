"""Sends video frames to the detector, and prints what it finds in them.

    python app.py [--source video.mp4]

The source is anything OpenCV opens: a video file, an image, or a camera
number. It plays in a loop. The default is a sample image from ultralytics.
"""

import argparse
import contextlib
import json
import signal
import time

import cv2
import gabriel
import numpy as np
from common import (
    CHANNELS,
    ENGINE,
    FLOW,
    FRAME_SIZE,
    HEIGHT,
    PRODUCER,
    SERVER,
    WIDTH,
)
from ultralytics.utils import ASSETS

FRAMES_PER_SECOND = 30
NANOSECONDS_PER_MILLISECOND = 1_000_000

# The detector gets the camera's newest frame each time it is free. The
# camera's tokens bound how many of its frames are in the flow at once: one
# per place a frame can wait (the edge and the engine), plus one.
FLOW_SPEC = f"""
[[producers]]
name = "{PRODUCER}"
tokens = 3

[[engines]]
name = "{ENGINE}"
location = "local"

[[edges]]
from = "{PRODUCER}"
to = "{ENGINE}"
timeout = "1s"
"""


def read_frame(video: cv2.VideoCapture, source: str) -> np.ndarray:
    """The next frame of `video`, starting it over at the end."""
    ok, frame = video.read()
    if not ok:
        video.open(source)
        ok, frame = video.read()
    if not ok:
        raise SystemExit(f"cannot read {source}")
    return frame


def describe(event: gabriel.Event, published: dict[int, int]) -> str:
    """What the detector found in a frame, and how old the frame is now."""
    objects = json.loads(event.result)
    labels = ", ".join(f"{o['label']} {o['confidence']:.0%}" for o in objects)
    age_ms = (
        time.monotonic_ns() - published[event.id.seq]
    ) / NANOSECONDS_PER_MILLISECOND
    return (
        f"frame {event.id.seq:5}: {age_ms:5.1f} ms old, "
        f"{len(objects)} objects: {labels}"
    )


parser = argparse.ArgumentParser()
parser.add_argument("--source", default=str(ASSETS / "bus.jpg"))
args = parser.parse_args()
video = cv2.VideoCapture(
    int(args.source) if args.source.isdigit() else args.source
)
# Stop on SIGTERM as on Ctrl-C.
signal.signal(signal.SIGTERM, signal.default_int_handler)

with (
    gabriel.Producer(PRODUCER, max_input_size=FRAME_SIZE) as camera,
    gabriel.Subscription(PRODUCER) as events,
    gabriel.Client("yolo-app", SERVER) as client,
    contextlib.suppress(KeyboardInterrupt),
):
    client.add_producer(PRODUCER)
    client.add_flow(FLOW, FLOW_SPEC)
    published = {}  # when each frame was published, by sequence number

    while True:
        # Resize the frame straight into shared memory, and publish it.
        buf = camera.loan()
        frame = np.frombuffer(buf, np.uint8, FRAME_SIZE).reshape(
            HEIGHT, WIDTH, CHANNELS
        )
        cv2.resize(read_frame(video, args.source), (WIDTH, HEIGHT), dst=frame)
        published[camera.publish(FRAME_SIZE, FLOW)] = time.monotonic_ns()

        # Print the detector's results until the next frame is due.
        deadline = time.monotonic() + 1 / FRAMES_PER_SECOND
        while (
            event := events.next(max(deadline - time.monotonic(), 0))
        ) is not None:
            if event.kind == gabriel.EventKind.RESULT:
                print(describe(event, published), flush=True)
                # Older frames get no result any more.
                published = {
                    seq: at
                    for seq, at in published.items()
                    if seq > event.id.seq
                }
