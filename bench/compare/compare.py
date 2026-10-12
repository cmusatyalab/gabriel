"""Compares the new Gabriel with the old one on this machine.

    python compare.py [--systems new old-ipc] [--resolutions 720p 4k]

For each system and frame size, it starts a server, an engine that answers at
once, and an app that sends a frame, waits for its result, and sends the next.
It reports each frame's round trip and the CPU each process used, prints a
table, and saves everything under bench-results/.
"""

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import common
import numpy as np
import psutil

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parents[1] / "bench-results"
# Each system: its script, and the transport it takes, if any.
SYSTEMS = {
    "new": ("new_gabriel.py", None),
    "old-websocket": ("old_gabriel.py", "websocket"),
    "old-websocket-uncompressed": (
        "old_gabriel.py",
        "websocket-uncompressed",
    ),
    "old-tcp": ("old_gabriel.py", "tcp"),
    "old-ipc": ("old_gabriel.py", "ipc"),
}
PERCENTILES = (50, 90, 99)
NS_PER_MS = 1e6
PERCENT = 100
# How long a process gets to stop before it is killed.
STOP_GRACE = 5.0  # seconds


class Process:
    """One process of a system, which prints its progress on standard out."""

    def __init__(self, command: list[str]) -> None:
        """Starts the process `command`."""
        self.popen = subprocess.Popen(
            command, stdout=subprocess.PIPE, text=True, cwd=HERE
        )
        self.ps = psutil.Process(self.popen.pid)

    def read_line(self) -> str:
        """Reads the next line the process prints."""
        line = self.popen.stdout.readline()
        if not line:
            code = self.popen.wait()
            raise RuntimeError(f"{self.popen.args} exited with {code}")
        return line.strip()

    def wait_for(self, expected: str) -> None:
        """Waits until the process prints `expected`."""
        while (line := self.read_line()) != expected:
            print(f"  unexpected output: {line}", file=sys.stderr)

    def cpu_s(self) -> float:
        """Gives the CPU time the process has used, in seconds."""
        times = self.ps.cpu_times()
        return times.user + times.system

    def stop(self) -> None:
        """Stops the process, or kills it if it does not stop in time."""
        self.popen.terminate()
        try:
            self.popen.wait(STOP_GRACE)
        except subprocess.TimeoutExpired:
            self.popen.kill()
            self.popen.wait()


def run(system: str, resolution: str, args) -> dict:
    """Runs one system at one frame size, and summarizes the run."""
    script, transport = SYSTEMS[system]
    with tempfile.TemporaryDirectory() as ipc_dir:

        def start(role: str) -> Process:
            command = [
                sys.executable,
                script,
                role,
                f"--resolution={resolution}",
                f"--warmup={args.warmup}",
                f"--frames={args.frames}",
            ]
            if transport:
                command += [f"--transport={transport}", f"--ipc-dir={ipc_dir}"]
            return Process(command)

        processes = []
        try:
            for role in ("server", "engine"):
                processes.append(start(role))
                processes[-1].wait_for(common.READY)
            server, engine = processes
            app = start("app")
            processes.append(app)

            app.wait_for(common.MEASURING)
            before = {"server": server.cpu_s(), "engine": engine.cpu_s()}
            measured = json.loads(app.read_line())
            after = {"server": server.cpu_s(), "engine": engine.cpu_s()}
        finally:
            for process in reversed(processes):
                process.stop()

    cpu_s = {name: after[name] - before[name] for name in before}
    cpu_s["app"] = measured["app_cpu_s"]
    return summarize(system, resolution, measured, cpu_s)


def summarize(system, resolution, measured, cpu_s) -> dict:
    """Gives the run's latency percentiles, frame rate and CPU use."""
    latencies_ms = np.array(measured["latencies_ns"]) / NS_PER_MS
    wall_s = measured["wall_s"]
    row = {"system": system, "resolution": resolution}
    for p, value in zip(PERCENTILES, np.percentile(latencies_ms, PERCENTILES)):
        row[f"p{p}_ms"] = round(float(value), 3)
    row["fps"] = round(len(latencies_ms) / wall_s, 1)
    for name, seconds in cpu_s.items():
        row[f"{name}_cpu_pct"] = round(PERCENT * seconds / wall_s, 1)
    row["latencies_ms"] = latencies_ms.round(3).tolist()
    return row


def print_table(rows: list[dict]) -> None:
    """Prints the summaries as a table."""
    columns = [key for key in rows[0] if key != "latencies_ms"]
    widths = [max(len(c), *(len(str(r[c])) for r in rows)) for c in columns]
    print("  ".join(c.rjust(w) for c, w in zip(columns, widths)))
    for row in rows:
        print("  ".join(str(row[c]).rjust(w) for c, w in zip(columns, widths)))


def main() -> None:
    """Runs each system at each frame size."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--systems", nargs="+", choices=SYSTEMS, default=list(SYSTEMS)
    )
    parser.add_argument(
        "--resolutions",
        nargs="+",
        choices=common.RESOLUTIONS,
        default=list(common.RESOLUTIONS),
    )
    parser.add_argument(
        "--warmup", type=int, default=30, help="frames sent before measuring"
    )
    parser.add_argument("--frames", type=int, default=300)
    args = parser.parse_args()
    if args.warmup < 1:
        parser.error("--warmup must be at least 1")

    out = RESULTS / f"compare-{int(time.time())}"
    out.mkdir(parents=True)
    rows = []
    for resolution in args.resolutions:
        for system in args.systems:
            print(f"{system} at {resolution} ...", file=sys.stderr, flush=True)
            rows.append(run(system, resolution, args))
            (out / f"{system}-{resolution}.json").write_text(
                json.dumps(rows[-1])
            )

    with open(out / "summary.csv", "w", newline="") as f:
        columns = [key for key in rows[0] if key != "latencies_ms"]
        writer = csv.DictWriter(f, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print_table(rows)
    print(f"saved in {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
