"""Runs the local server, which delivers the app's frames to the detector.

    python server.py

Start it first, then the detector and the app, each in its own terminal.
"""

import contextlib
import signal

import gabriel
from common import ENGINE, SERVER

# Stop on SIGTERM as on Ctrl-C.
signal.signal(signal.SIGTERM, signal.default_int_handler)

with gabriel.Server(SERVER) as server, contextlib.suppress(KeyboardInterrupt):
    # The engine may start before or after this.
    server.add_engine(ENGINE)
    print(f"serving engine {ENGINE!r}; press Ctrl-C to stop", flush=True)
    signal.pause()
