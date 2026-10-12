"""What the processes of both Gabriels share.

That is the frames, the measurement, and the lines a process prints to tell
the runner where it is.
"""

import argparse
import json
import signal
import sys
import time

import numpy as np

# Frame sizes, as (width, height). Frames are raw RGB, 3 bytes a pixel.
RESOLUTIONS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}
CHANNELS = 3
# What the engine returns for each frame: about the size of a few detections.
RESULT_SIZE = 256
RESULT = b"x" * RESULT_SIZE
# Inputs in flight at once. With one, each result is the round trip of one
# frame, with nothing queued behind it.
TOKENS = 1
# Far longer than any round trip, so a broken run fails rather than hangs.
WAIT = 30.0  # seconds

# Lines a process prints for the runner.
READY = "ready"
MEASURING = "measuring"


def role_arguments(transports: list[str] | None = None) -> argparse.Namespace:
    """Reads the arguments the runner gives each process of a system."""
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["server", "engine", "app"])
    parser.add_argument("--resolution", choices=RESOLUTIONS, required=True)
    parser.add_argument("--warmup", type=int, required=True)
    parser.add_argument("--frames", type=int, required=True)
    if transports:
        parser.add_argument("--transport", choices=transports, required=True)
        parser.add_argument("--ipc-dir", required=True)
    return parser.parse_args()


def make_frame(resolution: str) -> np.ndarray:
    """Makes a frame of noise, so no layer can compress it."""
    width, height = RESOLUTIONS[resolution]
    byte = np.iinfo(np.uint8)
    return np.random.default_rng().integers(
        byte.min, byte.max, (height, width, CHANNELS), np.uint8, endpoint=True
    )


def say(line: str) -> None:
    """Prints a line for the runner."""
    print(line, flush=True)


def stop_on_sigterm() -> None:
    """Stops on SIGTERM as on Ctrl-C, which raises KeyboardInterrupt."""
    signal.signal(signal.SIGTERM, signal.default_int_handler)


class Recorder:
    """Records each frame's round trip, after a warm-up.

    It tells the runner when the measured part starts, and prints the results
    as JSON at the end.
    """

    def __init__(self, warmup: int, frames: int) -> None:
        """Records `frames` round trips after the first `warmup`."""
        self.warmup, self.frames = warmup, frames
        self.latencies_ns: list[int] = []
        self._seen = 0

    @property
    def done(self) -> bool:
        """Whether all round trips are recorded."""
        return self._seen == self.warmup + self.frames

    def record(self, latency_ns: int) -> None:
        """Records the round trip of the next frame."""
        self._seen += 1
        if self._seen <= self.warmup:
            if self._seen == self.warmup:
                self._start()
            return
        self.latencies_ns.append(latency_ns)
        if self.done:
            self._finish()

    def _start(self) -> None:
        say(MEASURING)
        self._wall = time.perf_counter()
        self._cpu = time.process_time()

    def _finish(self) -> None:
        json.dump(
            {
                "latencies_ns": self.latencies_ns,
                "wall_s": time.perf_counter() - self._wall,
                "app_cpu_s": time.process_time() - self._cpu,
            },
            sys.stdout,
        )
        say("")
