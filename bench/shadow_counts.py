# /// script
# requires-python = ">=3.11"
# ///
"""Shadow run of the planned section counts on recorded runs.

    python3 bench/shadow_counts.py bench-results/chain-1791610586 [...]

The design note plans one token pool per section (``Sections'' in
docs/dag-tokens.tex), with a count set at runtime. This replays a run's
events in time order, as the client would see them, and keeps the estimates
the plan describes, without acting on them:

  S   per engine, its service time (Sent to Result), smoothed.
  p   per engine, the share of its results that pass the input on, smoothed.
      Visit ratios are products of these along a chain, not counts of the
      inputs that reached each engine, which today's skips distort.
  I   the producer's mean interval between inputs.

After each result, every section's knee and count are computed again:

  N* = (D + Z) / max(D_max, I)      N = max(1, floor(N* + tolerance))

with hysteresis on each change. Z is 0: the runs are local. A section starts
at the producer or at an edge leaving a fan-out, and everything below its
fan-out is one box: its time is that of the branch that completes first,
its demand the smallest bottleneck among its branches.
"""

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from recorded import Run, forwards, load, producer_interval, results

SERVICE_TIME_GAIN = 1 / 8
MIN_SAMPLES = 10
# A knee this close below a whole number counts as that number: equal
# engines give N* = 3 exactly, and noise puts it at 2.99.
ROUNDING_TOLERANCE = 0.1
COUNT_HYSTERESIS = 0.05
# The share of the run, from its end, whose estimates are summarized.
SUMMARY_SHARE = 0.5
SUMMARY_PERCENTILE = 0.05


class Ewma:
    """A smoothed value, moved by a fixed share of each new sample."""

    def __init__(self) -> None:
        self.value: float | None = None
        self.samples = 0

    def add(self, sample: float) -> None:
        if self.value is None:
            self.value = sample
        else:
            self.value += SERVICE_TIME_GAIN * (sample - self.value)
        self.samples += 1


@dataclass
class Section:
    """A chain of engines from an entry, ending at a fan-out, whose branches
    are sections of their own, or at the end of the path."""

    name: str
    engines: list[str]
    branches: list["Section"]
    paced_by_producer: bool = False
    count: int = 1
    changes: int = 0
    knees: list[float] = field(default_factory=list)

    def all(self):
        yield self
        for branch in self.branches:
            yield from branch.all()


def build(entry: str, targets: list[str], out: dict) -> Section:
    """The section that starts at `entry` with edges to `targets`."""
    engines = []
    while len(targets) == 1:
        engines.append(targets[0])
        targets = out.get(targets[0], [])
    last = engines[-1] if engines else entry
    branches = [build(f"{last}->{t}", [t], out) for t in targets]
    name = f"{entry}:{'>'.join(engines)}" if engines else entry
    return Section(name, engines, branches)


def demand(section: Section, s: dict, p: dict) -> tuple[float, float] | None:
    """(D + Z, D_max) per input entering the section, or None while an
    engine has too few samples."""
    visits = 1.0
    time = 0.0
    d_max = 0.0
    for engine in section.engines:
        if s[engine].samples < MIN_SAMPLES:
            return None
        d = visits * s[engine].value
        time += d
        d_max = max(d_max, d)
        visits *= p[engine].value
    if section.branches:
        parts = [demand(branch, s, p) for branch in section.branches]
        if None in parts:
            return None
        time += visits * min(t for t, _ in parts)
        d_max = max(d_max, visits * min(d for _, d in parts))
    return time, d_max


def next_count(count: int, knee: float) -> int:
    """The count after a new knee, changed only once the knee is clear of
    the boundary by the hysteresis."""
    rounded = knee + ROUNDING_TOLERANCE
    target = max(1, int(rounded))
    if target > count and rounded >= target + COUNT_HYSTERESIS:
        return target
    if target < count and rounded < count - COUNT_HYSTERESIS:
        return target
    return count


def shadow(run: Run) -> None:
    """Replay `run` and print each section's estimates."""
    flow = run.spec["flow"]
    out = defaultdict(list)
    for edge in flow["edges"]:
        out[edge["from"]].append(edge["to"])
    producer = flow["producers"][0]["name"]
    root = build(producer, out[producer], out)
    root.paced_by_producer = True
    interval, _ = producer_interval(run)

    s = defaultdict(Ewma)
    p = defaultdict(Ewma)
    for event, service in results(run):
        s[event["engine"]].add(service)
        p[event["engine"]].add(1.0 if forwards(event) else 0.0)
        for section in root.all():
            part = demand(section, s, p)
            if part is None:
                continue
            time, d_max = part
            pace = interval if section.paced_by_producer else 0.0
            knee = time / max(d_max, pace)
            section.knees.append(knee)
            count = next_count(section.count, knee)
            if count != section.count:
                section.changes += 1
                section.count = count

    print(f"{run.path.name}: {run.tokens} tokens in the run, "
          f"producer interval {interval:.1f} ms")
    for engine in sorted(s):
        print(f"  {engine:>10}: S {s[engine].value:6.1f} ms, "
              f"forwards {p[engine].value:4.2f}")
    for section in root.all():
        if not section.knees:
            print(f"  section {section.name}: no estimate")
            continue
        late = sorted(section.knees[int(len(section.knees) * SUMMARY_SHARE):])
        cut = int(len(late) * SUMMARY_PERCENTILE)
        print(f"  section {section.name:<24} N* {section.knees[-1]:4.2f} "
              f"(late p5-p95 {late[cut]:4.2f}-{late[-1 - cut]:4.2f}), "
              f"N {section.count}, changes {section.changes}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", type=Path, nargs="+",
                        help="runs' output directories")
    for path in parser.parse_args().runs:
        shadow(load(path))


if __name__ == "__main__":
    main()
