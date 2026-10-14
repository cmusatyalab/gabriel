# /// script
# requires-python = ">=3.11"
# ///
"""Simulate a chain with each token count, with and without skips.

    python3 bench/simulate_sections.py RUN_DIR [RUN_DIR ...]

Give the runs of one token sweep: the same chain, recorded with several
token counts. The bench takes the count from the run's file, so a sweep is
one copy of the file per count, e.g.

    for n in 1 2 3 4 7; do
      sed "s/^tokens = .*/tokens = $n/" bench/runs/slow-end.toml > t$n.toml
      cargo run --release -p gabriel-bench -- t$n.toml --out out-$n
    done
    python3 bench/simulate_sections.py out-*

For each count, it simulates the chain under two rules for an input that
finds its engine busy:

  newest   today: one pending slot per engine. A newer input replaces the
           waiting one, which is skipped and returns its token.
  order    planned for sections (``Sections'' in docs/dag-tokens.tex): the
           input waits in a queue, and the engine takes the oldest first.

Service times are drawn from those measured in the sweep's run with the
most tokens, and each engine passes inputs on with the share measured
there. The producer publishes at the measured interval, Poisson if the run's
arrivals were. The client admits its newest input when it has a token and
fewer than CROSSING_CAPACITY offers are in flight to the first engine, as
today. The newest rule is checked against the measured runs; the order rule
is the prediction.

Printed per count: results a second and mean AoI in ms at the last engine,
and the share of engine time spent on inputs that never completed.
"""

import argparse
import heapq
import random
from dataclasses import dataclass, field
from pathlib import Path

from recorded import (
    MS_PER_S,
    forward_shares,
    load,
    measured,
    producer_interval,
    service_samples,
    throughput_and_aoi,
)

# Today's crossing from a client to its local server (services.rs).
CROSSING_CAPACITY = 3
SIMULATED_S = 120.0
TRIM_S = 1.0
MAX_TOKENS = 7
SEED = 1
# Interval variation above this is taken as Poisson arrivals.
POISSON_CV2 = 0.5
# Counts whose AoI is within this share of the best count's tie with it.
AOI_TIE = 0.01
NEWEST = "newest"
ORDER = "order"


@dataclass
class Stage:
    """An engine of the chain: its service times, its share of results that
    pass the input on, and the inputs at it."""

    draws: list[float]
    passes: float
    busy: tuple | None = None  # (seq, published, started)
    waiting: list = field(default_factory=list)  # (seq, published)


def simulate(stages, interval, poisson, tokens, rule, seed=SEED):
    """Results a second and AoI at the last stage, and the share of engine
    time spent on inputs that never completed."""
    rng = random.Random(seed)
    for stage in stages:
        stage.busy, stage.waiting = None, []
    events = []  # (time, order, stage index or None for a publish)
    tie = 0
    now = 0.0
    available = tokens
    newest = None  # (seq, published), not yet admitted
    seq = 0
    in_flight = 0  # offers to the first engine
    work: dict[int, float] = {}  # seq -> engine time spent on it
    completed_work = 0.0
    total_work = 0.0
    done_at_last = []  # (time, published)

    def push(at, stage):
        nonlocal tie
        heapq.heappush(events, (at, tie, stage))
        tie += 1

    def end(seq, completed):
        nonlocal available, completed_work
        available += 1
        spent = work.pop(seq, 0.0)
        if completed:
            completed_work += spent

    def offer(i, item):
        nonlocal in_flight
        stage = stages[i]
        if rule == NEWEST and stage.waiting:
            replaced = stage.waiting.pop()
            if i == 0:
                in_flight -= 1
            end(replaced[0], completed=False)
        stage.waiting.append(item)

    def settle():
        # A free engine takes its waiting input before a new offer reaches
        # its slot: the server delivers at once, the client's offer later.
        nonlocal available, newest, in_flight
        changed = True
        while changed:
            changed = False
            for i, stage in enumerate(stages):
                if stage.busy is None and stage.waiting:
                    s, published = stage.waiting.pop(0)
                    stage.busy = (s, published, now)
                    push(now + rng.choice(stage.draws), i)
                    changed = True
            if (newest is not None and available > 0
                    and in_flight < CROSSING_CAPACITY):
                available -= 1
                in_flight += 1
                offer(0, newest)
                newest = None
                changed = True

    push(0.0, None)
    while events:
        now, _, i = heapq.heappop(events)
        if now > SIMULATED_S * MS_PER_S:
            break
        if i is None:
            seq += 1
            newest = (seq, now)
            gap = rng.expovariate(1 / interval) if poisson else interval
            push(now + gap, None)
        else:
            stage = stages[i]
            s, published, started = stage.busy
            stage.busy = None
            work[s] = work.get(s, 0.0) + now - started
            total_work += now - started
            if i == 0:
                in_flight -= 1
            if i == len(stages) - 1:
                done_at_last.append((now, published))
                end(s, completed=True)
            elif rng.random() < stage.passes:
                offer(i + 1, (s, published))
            else:
                end(s, completed=True)
        settle()

    start = TRIM_S * MS_PER_S
    stop = (SIMULATED_S - TRIM_S) * MS_PER_S
    rate, aoi = throughput_and_aoi(done_at_last, start, stop)
    wasted = max(0.0, 1 - completed_work / total_work) if total_work else 0.0
    return rate, aoi, wasted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", type=Path, nargs="+",
                        help="output directories of one token sweep")
    runs = {run.tokens: run for run in map(load, parser.parse_args().runs)}
    if None in runs:
        parser.error("each run of a sweep must give its token count")
    fit = runs[max(runs)]
    samples = service_samples(fit)
    shares = forward_shares(fit)
    stages = [Stage(samples[e], shares[e]) for e in fit.engines]
    interval, cv2 = producer_interval(fit)
    poisson = cv2 > POISSON_CV2
    last = fit.engines[-1]

    print(f"{fit.spec['name']}, at {last}:")
    print(f"  {'N':>2} | {'measured':^13} | {'newest':^20} | {'order':^20}")
    print(f"  {'':>2} | {'X/s':>6} {'AoI':>6} | {'X/s':>6} {'AoI':>6} "
          f"{'waste':>6} | {'X/s':>6} {'AoI':>6} {'waste':>6}")
    best = None
    for n in range(1, MAX_TOKENS + 1):
        xn, an, wn = simulate(stages, interval, poisson, n, NEWEST)
        xo, ao, wo = simulate(stages, interval, poisson, n, ORDER)
        if best is None or ao < best[1] * (1 - AOI_TIE):
            best = (n, ao)
        meas = " " * 13
        if n in runs:
            meas = "{:6.1f} {:6.0f}".format(*measured(runs[n], last))
        print(f"  {n:>2} | {meas} | {xn:6.1f} {an:6.0f} {wn:6.0%} "
              f"| {xo:6.1f} {ao:6.0f} {wo:6.0%}")
    print(f"  in order, the lowest AoI is at N = {best[0]}")


if __name__ == "__main__":
    main()
