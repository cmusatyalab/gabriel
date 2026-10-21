"""Runs YOLO26 as a Gabriel engine: finds the objects in each frame it gets.

    python detector.py [--model yolo26n.pt]

Each result is a JSON list of objects: their label, confidence, and box in
pixels (x1, y1, x2, y2).
"""

import argparse
import contextlib
import json
import signal

import gabriel
import numpy as np
from common import CHANNELS, ENGINE, HEIGHT, WIDTH
from ultralytics import YOLO

MAX_RESULT_SIZE = 64 * 1024
RESULT_TYPE = "application/json"

parser = argparse.ArgumentParser()
parser.add_argument(
    "--model", default="yolo26n.pt", help="downloaded if missing"
)
args = parser.parse_args()

model = YOLO(args.model)
# The first frame is slow while the GPU warms up, so spend it on a blank one.
model(np.zeros((HEIGHT, WIDTH, CHANNELS), np.uint8), verbose=False)
# Stop on SIGTERM as on Ctrl-C.
signal.signal(signal.SIGTERM, signal.default_int_handler)

with (
    gabriel.Engine(
        ENGINE, result_type=RESULT_TYPE, max_output_size=MAX_RESULT_SIZE
    ) as engine,
    contextlib.suppress(KeyboardInterrupt),
):
    print(f"engine {ENGINE!r} ready", flush=True)
    for input in engine:
        # The frame stays in shared memory: no copy until YOLO reads it.
        frame = np.frombuffer(input.data, np.uint8).reshape(
            HEIGHT, WIDTH, CHANNELS
        )
        found = model(frame, verbose=False)[0]
        objects = [
            {
                "label": found.names[int(cls)],
                "confidence": round(float(conf), 2),
                "box": [round(float(x)) for x in box],
            }
            for cls, conf, box in zip(
                found.boxes.cls, found.boxes.conf, found.boxes.xyxy
            )
        ]
        engine.drop(result=json.dumps(objects).encode())
