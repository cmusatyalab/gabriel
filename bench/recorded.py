"""Reading a recorded benchmark run without pandas: service times, the share
of results each engine forwards, the producer's interval, the throughput and
age of information at an engine, and wasted work.

Used by shadow_counts.py and simulate_sections.py. Times are in ms.

A remote server elsewhere stamps its events with its own clock, so a time
from it is only compared with another from it, as in service times. The
device knows of a remote event when the client receives it.
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
    work: list[dict]  # work.csv rows: what each engine worked on, and how long

    @property
    def tokens(self) -> int | None:
        """The first producer's token count, or None if the client computed
        it. A sweep gives it in each run."""
        return self.spec["flow"]["producers"][0].get("tokens")

    @property
    def engines(self) -> list[str]:
        """The engines, in the order the spec lists them."""
        return [engine["name"] for engine in self.spec["flow"]["engines"]]


def known_ns(event: dict) -> int:
    """When the device knew of `event`, on its clock: when the client
    received it, for a remote server's event."""
    return int(event.get("received_ns") or event["at_ns"])


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
    events.sort(key=known_ns)
    # Runs recorded before the bench wrote work.csv have none.
    work = []
    if (path / "work.csv").exists():
        with open(path / "work.csv") as f:
            work = list(csv.DictReader(f))
    return Run(path, spec, published, events, work)


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
    """Whether a Result forwarded its input or a replacement."""
    # Recordings made before the outcomes were renamed call the field
    # "forward", and a drop "Stop".
    if "outcome" in event:
        return event["outcome"] != "Drop"
    return event["forward"] != "Stop"


def service_samples(run: Run) -> dict[str, list[float]]:
    """Every service time measured, per engine."""
    samples = defaultdict(list)
    for event, service in results(run):
        samples[event["engine"]].append(service)
    return samples


def work_rows(run: Run) -> Iterator[tuple[str, str, float]]:
    """Each engine's own work per input, as (engine, seq, ms), without
    waiting for a shared accelerator. For engines the bench did not record
    work for, such as external ones, the service times stand in for it, so
    their waits for a shared accelerator count as work."""
    recorded = set()
    for row in run.work:
        recorded.add(row["engine"])
        yield row["engine"], row["seq"], int(row["work_ns"]) / NS_PER_MS
    for event, service in results(run):
        if event["engine"] not in recorded:
            yield event["engine"], event["seq"], service


def work_samples(run: Run) -> dict[str, list[float]]:
    """Every engine's own work per input, as work_rows gives it."""
    samples = defaultdict(list)
    for engine, _, work in work_rows(run):
        samples[engine].append(work)
    return samples


def accelerators(run: Run) -> dict[str, str | None]:
    """The accelerator each engine works on, if it shares one."""
    return {name: load.get("accelerator")
            for name, load in run.spec["engines"].items()}


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
    """Results a second and mean AoI in ms at `engine`, as the app sees its
    results."""
    published = run.published_ns.values()
    start = (min(published) / NS_PER_MS) + trim_s * MS_PER_S
    end = (max(published) / NS_PER_MS) - trim_s * MS_PER_S
    at = [
        (known_ns(e) / NS_PER_MS, run.published_ns[e["seq"]] / NS_PER_MS)
        for e in run.events
        if e["kind"] == "Result"
        and e["engine"] == engine
        and e["seq"] in run.published_ns
    ]
    return throughput_and_aoi(at, start, end)


def wasted_work(
    run: Run, trim_s: float = DEFAULT_TRIM_S
) -> dict[str, tuple[float, float]]:
    """Per engine, the ms of work wasted and the ms of work done, on the
    inputs published away from the ends of the run.

    A run of an engine is used if the engine stopped the input (a filter's
    decision is a result), if nothing comes after the engine, or if some
    engine after it used the input. Otherwise its work was wasted: every
    engine after it skipped the input or timed out. Work is as work_rows
    gives it."""
    children = defaultdict(list)
    for edge in run.spec["flow"]["edges"]:
        children[edge["from"]].append(edge["to"])
    decided = {}  # (engine, seq) -> whether it passed the input on
    for event in run.events:
        if event["kind"] == "Result":
            decided[event["engine"], event["seq"]] = forwards(event)

    used = {}

    def is_used(engine: str, seq: str) -> bool:
        key = (engine, seq)
        if key not in used:
            passed = decided.get(key)
            used[key] = passed is not None and (
                not passed
                or not children[engine]
                or any(is_used(child, seq) for child in children[engine])
            )
        return used[key]

    published = run.published_ns.values()
    start = min(published) + trim_s * MS_PER_S * NS_PER_MS
    end = max(published) - trim_s * MS_PER_S * NS_PER_MS
    totals = defaultdict(lambda: [0.0, 0.0])
    for engine, seq, work in work_rows(run):
        if not start <= run.published_ns.get(seq, -1) <= end:
            continue
        totals[engine][1] += work
        if not is_used(engine, seq):
            totals[engine][0] += work
    return {engine: (w, t) for engine, (w, t) in totals.items()}
