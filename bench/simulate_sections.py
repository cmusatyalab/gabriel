# /// script
# requires-python = ">=3.11"
# ///
"""Simulate a chain with each token count: today's rule, sections, and gated
chains.

    python3 bench/simulate_sections.py RUN_DIR [RUN_DIR ...]
    python3 bench/simulate_sections.py --chain 20,30,500 [--jitter 0.1]

Give the runs of one token sweep: the same chain, recorded with several
token counts. The bench takes the count from the run's file, so a sweep is
one copy of the file per count, e.g.

    for n in 1 2 3 4 7; do
      sed "s/^tokens = .*/tokens = $n/" bench/runs/slow-end.toml > t$n.toml
      cargo run --release -p gabriel-bench -- t$n.toml --out out-$n
    done
    python3 bench/simulate_sections.py out-*

For each count, it simulates the chain under three rules:

  newest   today: one pending slot per engine. A newer input replaces the
           waiting one, which is skipped and returns its token.
  order    sections (``Sections'' in docs/dag-tokens.tex): an input that
           finds its engine busy waits in a queue, and the engine takes the
           oldest first.
  gated    gated chains (docs/wasted-work.tex): newest wins, as today, but an
           engine with a slower engine after it starts an input only when
           the next engine's slot is empty, and no earlier than the slowest
           engine after it (its anchor) will want the result. That time is
           when the anchor is expected to be free for it, after the inputs
           already on their way, minus the median service times from this
           engine to the anchor. Engines with nothing slower after them run
           as today.

Service times are drawn from those measured in the sweep's run with the
most tokens (the engines' own work, if the run recorded it), and each
engine passes inputs on with the share measured there. The producer
publishes at the measured interval, Poisson if the run's arrivals were. The
client admits its newest input when it has a token and fewer than
CROSSING_CAPACITY offers are in flight to the first engine, as today. If
the run's engines share an accelerator, so do the simulated ones. The newest
rule is checked against the measured runs; the others are the prediction.

With --chain, it simulates a chain with the given median service times in
ms instead, each drawn from a normal distribution with the given relative
spread, every engine passing every input on, and a camera at CAMERA_FPS.

Printed per count: results a second and mean AoI in ms at the last engine,
and the share of engine time spent on inputs that never completed.
"""

import argparse
import heapq
import random
import statistics
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from recorded import (
    MS_PER_S,
    accelerators,
    forward_shares,
    load,
    measured,
    producer_interval,
    throughput_and_aoi,
    wasted_work,
    work_samples,
)

# Today's crossing from a client to its local server (services.rs).
CROSSING_CAPACITY = 3
SIMULATED_S = 120.0
TRIM_S = 1.0
MAX_TOKENS = 7
SEED = 1
# Interval variation above this is taken as Poisson arrivals.
POISSON_CV2 = 0.5
# The camera of a --chain, and the service times drawn for each engine.
CAMERA_FPS = 30.0
CHAIN_DRAWS = 5000
CHAIN_SEED = 2
# A drawn service time is at least this, in ms.
MIN_SERVICE_MS = 0.1
# Counts whose AoI is within this share of the best count's tie with it.
AOI_TIE = 0.01
NEWEST = "newest"
ORDER = "order"
GATED = "gated"
RULES = (NEWEST, ORDER, GATED)
# What an event in the simulation is, besides an engine finishing.
PUBLISH = "publish"
WAKE = "wake"  # a gated engine's start time came


@dataclass
class Stage:
    """An engine of the chain: its service times, its share of results that
    pass the input on, and the inputs at it."""

    draws: list[float]
    passes: float
    busy: tuple | None = None  # (seq, published, started, work)
    waiting: list = field(default_factory=list)  # (seq, published)
    median: float = field(init=False)

    def __post_init__(self):
        self.median = statistics.median(self.draws)


def anchors(stages):
    """Each engine's anchor under the gated rule: the slowest engine after
    it, if that one is slower, or None."""
    result = []
    for i, stage in enumerate(stages):
        after = range(i + 1, len(stages))
        anchor = max(after, key=lambda j: stages[j].median, default=None)
        slower = anchor is not None and stages[anchor].median > stage.median
        result.append(anchor if slower else None)
    return result


def simulate(stages, interval, poisson, tokens, rule, seed=SEED,
             shared=False):
    """Results a second and AoI at the last stage, and the share of engine
    work spent on inputs that never completed. With `shared`, the engines
    take turns on one accelerator, in the order they took their inputs."""
    rng = random.Random(seed)
    for stage in stages:
        stage.busy, stage.waiting = None, []
    anchor_of = anchors(stages)
    events = []  # (time, order, stage index, PUBLISH, or WAKE)
    wakes = set()  # (stage index, time) of the wakes pushed
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
    accelerator_busy = False
    turns = deque()  # engines holding an input, waiting for the accelerator

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
        if rule != ORDER and stage.waiting:
            replaced = stage.waiting.pop()
            if i == 0:
                in_flight -= 1
            end(replaced[0], completed=False)
        stage.waiting.append(item)

    def free_at(j):
        """When engine j is expected to finish what it holds."""
        busy = stages[j].busy
        return now if busy is None else max(now, busy[2] + stages[j].median)

    def anchor_free(i, anchor):
        """When engine `anchor` is expected to be free for engine i's next
        result: after the inputs already between them have gone through,
        each engine taking them in order at its median time."""
        free = [free_at(j) for j in range(anchor + 1)]
        ahead = []  # (the engine it goes to next, when it is ready there)
        for j in range(i + 1, anchor + 1):
            if stages[j].busy is not None and j < anchor:
                ahead.append((j + 1, free[j]))
            ahead.extend((j, now) for _ in stages[j].waiting)
        # The inputs furthest along go first.
        for first, ready in sorted(ahead, key=lambda a: -a[0]):
            for k in range(first, anchor + 1):
                free[k] = ready = max(ready, free[k]) + stages[k].median
        return free[anchor]

    def may_start(i):
        """Whether engine i may start its waiting input now. A gated engine
        that must wait gets a wake at its start time."""
        anchor = anchor_of[i]
        if rule != GATED or anchor is None:
            return True
        if stages[i + 1].waiting:
            return False
        between = sum(stage.median for stage in stages[i:anchor])
        start_at = anchor_free(i, anchor) - between
        if now >= start_at:
            return True
        if (i, start_at) not in wakes:
            wakes.add((i, start_at))
            push(start_at, WAKE)
        return False

    def take_turn(i):
        """Engine i starts work on its input, or waits for the
        accelerator."""
        nonlocal accelerator_busy
        if shared and accelerator_busy:
            turns.append(i)
            return
        accelerator_busy = shared
        push(now + stages[i].busy[3], i)

    def settle():
        # A free engine takes its waiting input before a new offer reaches
        # its slot: the server delivers at once, the client's offer later.
        nonlocal available, newest, in_flight
        changed = True
        while changed:
            changed = False
            for i, stage in enumerate(stages):
                if stage.busy is None and stage.waiting and may_start(i):
                    s, published = stage.waiting.pop(0)
                    stage.busy = (s, published, now, rng.choice(stage.draws))
                    take_turn(i)
                    changed = True
            if (newest is not None and available > 0
                    and in_flight < CROSSING_CAPACITY):
                available -= 1
                in_flight += 1
                offer(0, newest)
                newest = None
                changed = True

    push(0.0, PUBLISH)
    while events:
        now, _, i = heapq.heappop(events)
        if now > SIMULATED_S * MS_PER_S:
            break
        if i == WAKE:
            pass
        elif i == PUBLISH:
            seq += 1
            newest = (seq, now)
            gap = rng.expovariate(1 / interval) if poisson else interval
            push(now + gap, PUBLISH)
        else:
            stage = stages[i]
            s, published, _, spent = stage.busy
            stage.busy = None
            work[s] = work.get(s, 0.0) + spent
            total_work += spent
            accelerator_busy = False
            if turns:
                take_turn(turns.popleft())
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


def parse_medians(text):
    """The median service times of a --chain, e.g. "20,30,500"."""
    return [float(median) for median in text.split(",")]


def chain(medians, jitter):
    """A chain of engines with the given median service times."""
    rng = random.Random(CHAIN_SEED)
    return [
        Stage([max(MIN_SERVICE_MS, rng.gauss(median, jitter * median))
               for _ in range(CHAIN_DRAWS)], passes=1.0)
        for median in medians
    ]


def measured_waste(run):
    """The share of engine work a recorded run wasted, as printed."""
    totals = wasted_work(run).values()
    total = sum(t for _, t in totals)
    if not run.work or not total:
        return f"{'':>6}"  # recorded before the bench wrote work.csv
    return f"{sum(w for w, _ in totals) / total:6.0%}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", type=Path, nargs="*",
                        help="output directories of one token sweep")
    parser.add_argument("--chain", type=parse_medians,
                        help="median service times in ms, e.g. 20,30,500")
    parser.add_argument("--jitter", type=float, default=0.1,
                        help="relative spread of a --chain's service times")
    parser.add_argument("--shared", action="store_true",
                        help="the engines of a --chain share one accelerator")
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS,
                        help="the largest token count to simulate")
    args = parser.parse_args()
    if bool(args.runs) == bool(args.chain):
        parser.error("give either recorded runs or --chain")

    runs = {}
    if args.chain:
        stages = chain(args.chain, args.jitter)
        interval, poisson = MS_PER_S / CAMERA_FPS, False
        shared = args.shared
        title = "chain " + " > ".join(f"{m:g}" for m in args.chain)
        title += f" ms, jitter {args.jitter:g}"
        title += ", one shared accelerator" if shared else ""
    else:
        runs = {run.tokens: run for run in map(load, args.runs)}
        if None in runs:
            parser.error("each run of a sweep must give its token count")
        fit = runs[max(runs)]
        on = set(accelerators(fit).values())
        if len(on) > 1:
            parser.error("the simulator models one accelerator that every "
                         "engine shares, or none")
        shared = on != {None}
        samples = work_samples(fit)
        shares = forward_shares(fit)
        stages = [Stage(samples[e], shares[e]) for e in fit.engines]
        interval, cv2 = producer_interval(fit)
        poisson = cv2 > POISSON_CV2
        title = f"{fit.spec['name']}, at {fit.engines[-1]}"
        title += ", one shared accelerator" if shared else ""
        last = fit.engines[-1]

    print(f"{title}:")
    print(f"  {'N':>2} | {'measured':^20} | "
          + " | ".join(f"{rule:^20}" for rule in RULES))
    print(f"  {'':>2} | "
          + " | ".join(f"{'X/s':>6} {'AoI':>6} {'waste':>6}"
                       for _ in range(len(RULES) + 1)))
    best = None
    for n in range(1, args.max_tokens + 1):
        cells = []
        for rule in RULES:
            x, aoi, wasted = simulate(stages, interval, poisson, n, rule,
                                      shared=shared)
            cells.append(f"{x:6.2f} {aoi:6.0f} {wasted:6.0%}")
            better = best is None or aoi < best[1] * (1 - AOI_TIE)
            if rule == ORDER and better:
                best = (n, aoi)
        meas = " " * 20
        if n in runs:
            x, aoi = measured(runs[n], last)
            meas = f"{x:6.2f} {aoi:6.0f} {measured_waste(runs[n])}"
        print(f"  {n:>2} | {meas} | " + " | ".join(cells))
    print(f"  in order, the lowest AoI is at N = {best[0]}")


if __name__ == "__main__":
    main()
