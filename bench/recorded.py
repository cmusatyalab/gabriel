"""Reading a recorded benchmark run without pandas: service times, the share
of results each engine forwards, the producer's interval, and the throughput
and age of information at an engine.

Used by shadow_counts.py and simulate_sections.py. Times are in ms.
"""

import csv
import statistics
import tomllib
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

NS_PER_MS = 1e6
MS_PER_S = 1e3
# Results this close to the start or the end of a run are left out of
# throughput and AoI, as in analyze.py.
DEFAULT_TRIM_S = 1.0


@dataclass
class Run:
    """A run's description and rows."""

    path: Path
    spec: dict
    published_ns: dict[str, int]  # seq -> when the input was published
    events: list[dict]  # events.csv rows, in time order

    @property
    def tokens(self) -> int:
        return self.spec["flow"]["producers"][0]["tokens"]

    @property
    def engines(self) -> list[str]:
        """The engines, in the order the spec lists them."""
        return [engine["name"] for engine in self.spec["flow"]["engines"]]


def load(path: Path) -> Run:
    """Read the run in the output directory `path`."""
    spec = tomllib.loads((path / "run.toml").read_text())
    with open(path / "inputs.csv") as f:
        published = {
            r["seq"]: int(r["published_ns"]) for r in csv.DictReader(f)
        }
    with open(path / "events.csv") as f:
        events = list(csv.DictReader(f))
    events = [e for e in events if e["at_ns"]]
    events.sort(key=lambda e: int(e["at_ns"]))
    return Run(path, spec, published, events)


def results(run: Run) -> Iterator[tuple[dict, float]]:
    """Each Result that answers a Sent, with its service time: from the
    engine's Sent to its Result. Both are stamped by the engine's server."""
    sent = {}
    for event in run.events:
        key = (event["seq"], event["engine"])
        if event["kind"] == "Sent":
            sent[key] = int(event["at_ns"])
        elif event["kind"] == "Result" and key in sent:
            yield event, (int(event["at_ns"]) - sent.pop(key)) / NS_PER_MS


def forwards(event: dict) -> bool:
    """Whether a Result passed its input on."""
    return event["forward"] != "Stop"


def service_samples(run: Run) -> dict[str, list[float]]:
    """Every service time measured, per engine."""
    samples = defaultdict(list)
    for event, service in results(run):
        samples[event["engine"]].append(service)
    return samples


def forward_shares(run: Run) -> dict[str, float]:
    """The share of each engine's results that passed the input on."""
    decisions = defaultdict(list)
    for event, _ in results(run):
        decisions[event["engine"]].append(forwards(event))
    return {engine: statistics.fmean(d) for engine, d in decisions.items()}


def producer_interval(run: Run) -> tuple[float, float]:
    """The mean gap between published inputs, and its squared coefficient of
    variation: 0 for a camera, 1 for Poisson arrivals."""
    published = sorted(run.published_ns.values())
    gaps = [(b - a) / NS_PER_MS for a, b in zip(published, published[1:])]
    mean = statistics.fmean(gaps)
    return mean, statistics.pvariance(gaps) / mean**2


def throughput_and_aoi(
    results_at: list[tuple[float, float]], start: float, end: float
) -> tuple[float, float]:
    """Results a second, and the time-average age of the newest result, over
    [start, end]. `results_at` holds (result time, publish time) in ms, in
    time order."""
    count = sum(1 for t, _ in results_at if start <= t <= end)
    area, last_t, newest = 0.0, None, None
    for t, published in results_at:
        if last_t is not None:
            a, b = max(last_t, start), min(t, end)
            if b > a:
                area += ((a - newest) + (b - newest)) / 2 * (b - a)
        newest = published if newest is None else max(newest, published)
        last_t = t
    return count / ((end - start) / MS_PER_S), area / (end - start)


def measured(run: Run, engine: str, trim_s: float = DEFAULT_TRIM_S):
    """Results a second and mean AoI in ms at `engine`."""
    published = run.published_ns.values()
    start = (min(published) / NS_PER_MS) + trim_s * MS_PER_S
    end = (max(published) / NS_PER_MS) - trim_s * MS_PER_S
    at = [
        (int(e["at_ns"]) / NS_PER_MS, run.published_ns[e["seq"]] / NS_PER_MS)
        for e in run.events
        if e["kind"] == "Result"
        and e["engine"] == engine
        and e["seq"] in run.published_ns
    ]
    return throughput_and_aoi(at, start, end)
