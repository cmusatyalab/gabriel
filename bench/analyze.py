# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas>=2.2"]
# ///
"""Summarize a benchmark run in queueing terms: load, waiting, and freshness.

    uv run bench/analyze.py bench-results/chain-1791610586

Per engine, each a server that works on one input at a time:
  offers/s     arrival rate: offers made to the engine's edges.
  results/s    throughput X.
  S            service time, result - sent, and its coefficient of variation.
  load         offered load rho = arrival rate x mean S. Above 1, more
               candidates arrive than the engine can serve, and the rest are
               skipped: the price of always having a fresh one.
  busy         utilization U = X x mean S: the throughput side.
  skipped      the share of offers skipped in the pending slot.

Per input an engine got:
  age          sent - published: how old the input was when the engine got
               it.
  excess       the age minus the time the input spent in engines before this
               one on its path. All the waiting Gabriel added.
  before       offered - published at the first engine: waiting for a token
               and on the crossing, and the trip to the server.
  edge_wait    sent - offered: waiting at this engine's edge on the server.
  idle         sent - the engine's previous result: how long the engine sat
               idle before it got this input.

Age of information (AoI), per engine: at each moment, the age of the input
behind the engine's newest result. Its time average, and its peaks just
before each new result.

Per producer, by Little's law: the average number of its inputs in flight,
from reaching the server to their last event, against its tokens.

A result and the offers it makes share one timestamp, so an offer's maker is
the result whose time equals the offer's.
"""

import argparse
import tomllib
from pathlib import Path

import pandas as pd

NS_PER_MS = 1e6
NS_PER_S = 1e9
MS_PER_S = 1e3
PERCENTILES = [0.5, 0.9, 0.99]
INPUT = ["producer", "session", "seq", "flow"]
# The events that end an offer at an engine.
ENDS = ["Result", "Skipped", "TimedOut"]
WAITS = ["age", "excess", "before", "edge_wait", "idle"]
# Inputs published this close to the start or the end of a run are left out
# of waits and ages: engines are still starting, or no newer input comes to
# replace a waiting one.
DEFAULT_TRIM_S = 1.0


def load(run: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Read a run's published inputs, its events, and its description."""
    inputs = pd.read_csv(run / "inputs.csv")
    events = pd.read_csv(
        run / "events.csv", dtype={"engine": str, "flow": str}
    )
    spec = tomllib.loads((run / "run.toml").read_text())
    return inputs, events, spec


def offers(inputs: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """One row per offer sent to an engine: how it ended, and the waits."""
    sent = events[events.kind == "Sent"]
    ended = events[events.kind.isin(ENDS)][
        [*INPUT, "engine", "offered_ns", "kind", "at_ns"]
    ]
    offers = sent.merge(
        ended.rename(columns={"kind": "end", "at_ns": "ended_ns"}),
        on=[*INPUT, "engine", "offered_ns"],
        how="left",
    ).merge(inputs, on=["producer", "session", "seq"], how="left")
    offers["sent_ns"] = offers.at_ns
    offers["age"] = offers.sent_ns - offers.published_ns
    offers["edge_wait"] = offers.sent_ns - offers.offered_ns
    offers["service"] = (offers.ended_ns - offers.sent_ns).where(
        offers.end == "Result"
    )

    # Walk each offer back to the result that made it, in the order offers
    # were made, so a parent's waiting is known before its children's.
    made_by = {
        (*key, ended_ns): index
        for index, key, ended_ns in zip(
            offers.index,
            offers[INPUT].itertuples(index=False, name=None),
            offers.ended_ns,
            strict=True,
        )
        if not pd.isna(ended_ns)
    }
    excess, before = {}, {}
    for index, row in offers.sort_values("offered_ns").iterrows():
        parent = made_by.get((*row[INPUT], row.offered_ns))
        if parent is None:
            before[index] = row.offered_ns - row.published_ns
            excess[index] = before[index] + row.edge_wait
        else:
            excess[index] = excess[parent] + row.edge_wait
    offers["before"] = pd.Series(before)
    offers["excess"] = pd.Series(excess)

    # Each engine works on one input at a time, so the time from one result
    # to the next send is idle time.
    offers = offers.sort_values("sent_ns")
    previous = offers.groupby("engine")[["end", "ended_ns"]].shift()
    offers["idle"] = (offers.sent_ns - previous.ended_ns).where(
        previous.end == "Result"
    )
    return offers


def window(inputs: pd.DataFrame, trim_s: float) -> tuple[float, float]:
    """The part of the run every measure is taken from, in ns."""
    trim_ns = trim_s * NS_PER_S
    return (
        inputs.published_ns.min() + trim_ns,
        inputs.published_ns.max() - trim_ns,
    )


def engine_load(
    events: pd.DataFrame, offers: pd.DataFrame, start: float, end: float
) -> pd.DataFrame:
    """Per engine: arrival rate, throughput, service time, load, use.

    Counts events and offers sent between `start` and `end`.
    """
    seconds = (end - start) / NS_PER_S
    at_engines = events[
        events.engine.notna() & events.at_ns.between(start, end)
    ]
    sent = offers[offers.sent_ns.between(start, end)]
    # Offers the server made: sent ones, and ones skipped in its slots.
    made = at_engines[
        (at_engines.kind == "Sent")
        | ((at_engines.kind == "Skipped") & at_engines.offered_ns.notna())
    ]
    rows = {}
    for engine, group in sent.groupby("engine"):
        kinds = at_engines[at_engines.engine == engine].kind.value_counts()
        offered = (made.engine == engine).sum()
        service = group.service.dropna() / NS_PER_S
        mean_s = service.mean()
        rows[engine] = {
            "offers/s": offered / seconds,
            "results/s": kinds.get("Result", 0) / seconds,
            "S mean ms": mean_s * MS_PER_S,
            "S cv": service.std() / mean_s,
            "load": offered / seconds * mean_s,
            "busy": busy_ns(offers[offers.engine == engine], start, end)
            / NS_PER_S
            / seconds,
            "skipped": kinds.get("Skipped", 0) / max(offered, 1),
            "timed out": kinds.get("TimedOut", 0),
        }
    return pd.DataFrame(rows).T


def busy_ns(offers: pd.DataFrame, start: float, end: float) -> float:
    """How long the engine worked between `start` and `end`."""
    worked = offers.ended_ns.clip(upper=end) - offers.sent_ns.clip(lower=start)
    return worked.where(offers.end == "Result").clip(lower=0).sum()


def waits(offers: pd.DataFrame) -> pd.DataFrame:
    """Per engine: percentiles of each wait, in ms."""
    in_ms = offers.set_index("engine")[WAITS] / NS_PER_MS
    return in_ms.groupby("engine").quantile(PERCENTILES).unstack()


def age_of_information(
    offers: pd.DataFrame, start: float, end: float
) -> pd.DataFrame:
    """Per engine: the time-average AoI of its results, and its peaks, in ms.

    Results are taken at the time the server got them; the app hears of them
    a few microseconds later.
    """
    rows = {}
    results = offers[offers.end == "Result"]
    for engine, group in results.groupby("engine"):
        group = group.sort_values("ended_ns")
        times = group.ended_ns.to_numpy()
        # The newest input behind any result so far.
        newest = group.published_ns.cummax().to_numpy()
        keep = (times[:-1] >= start) & (times[1:] <= end)
        if not keep.any():
            continue
        t0, t1, g = times[:-1][keep], times[1:][keep], newest[:-1][keep]
        # Between two results the age grows linearly from t0 - g to t1 - g.
        area = (t1 - t0) * ((t0 + t1) / 2 - g)
        peaks = pd.Series(t1 - g) / NS_PER_MS
        rows[engine] = {
            "mean": area.sum() / (t1 - t0).sum() / NS_PER_MS,
            "peak p50": peaks.quantile(0.5),
            "peak p99": peaks.quantile(0.99),
        }
    return pd.DataFrame(rows).T


def in_flight(
    inputs: pd.DataFrame,
    events: pd.DataFrame,
    spec: dict,
    start: float,
    end: float,
) -> pd.DataFrame:
    """Per producer: inputs in flight on average (Little's L), and at most.

    An input counts from its first offer at the server to its last event.
    At a fan-out, an input can stay in flight after its token came back, so
    these can exceed the tokens there. The average is over `start` to `end`.
    """
    timed = events.dropna(subset=["seq"])
    first = timed.groupby(["producer", "session", "seq"]).offered_ns.min()
    last = timed.groupby(["producer", "session", "seq"]).at_ns.max()
    spans = pd.DataFrame({"start": first, "end": last}).dropna()
    tokens = {p["name"]: p["tokens"] for p in spec["flow"]["producers"]}
    seconds = (end - start) / NS_PER_S
    published = inputs[inputs.published_ns.between(start, end)]
    rows = {}
    for producer, group in spans.groupby(level="producer"):
        changes = pd.concat(
            [
                pd.Series(1, index=group.start.to_numpy()),
                pd.Series(-1, index=group.end.to_numpy()),
            ]
        ).sort_index(kind="stable")
        inside = group.end.clip(upper=end) - group.start.clip(lower=start)
        rows[producer] = {
            "published/s": (published.producer == producer).sum() / seconds,
            "reached server/s": group.start.between(start, end).sum()
            / seconds,
            "in flight mean": inside.clip(lower=0).sum() / NS_PER_S / seconds,
            "in flight max": changes.cumsum().max(),
            "tokens": tokens.get(producer),
        }
    return pd.DataFrame(rows).T


def main() -> None:
    """Print the summary of the run named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="a run's output directory")
    parser.add_argument(
        "--trim",
        type=float,
        default=DEFAULT_TRIM_S,
        help="seconds left out of waits and ages at each end of the run",
    )
    args = parser.parse_args()

    inputs, events, spec = load(args.run)
    all_offers = offers(inputs, events)
    start, end = window(inputs, args.trim)
    kept = all_offers[all_offers.published_ns.between(start, end)]
    for kind in ["Disconnected", "Lost"]:
        if (count := (events.kind == kind).sum()) > 0:
            print(f"{kind}: {count}")
    pd.set_option("display.float_format", "{:.2f}".format)
    print(f"measured without the first and last {args.trim:g} s of the run\n")
    print("producers:")
    print(in_flight(inputs, events, spec, start, end).to_string())
    print("\nengines:")
    print(engine_load(events, all_offers, start, end).to_string())
    print("\nwaits in ms, at the 50th, 90th and 99th percentiles:")
    print(waits(kept).T.to_string())
    print("\nage of information of each engine's results, in ms:")
    print(age_of_information(all_offers, start, end).to_string())


if __name__ == "__main__":
    main()
