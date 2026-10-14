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
  uplink       offered - the previous engine's result, when the two engines
               are on different servers: waiting on the client's crossing,
               and the trip to the remote server.
  edge_wait    sent - offered: waiting at this engine's edge on the server.
  idle         sent - the engine's previous result: how long the engine sat
               idle before it got this input.
  back         received - result, for a remote engine: the result's trip
               back to the client.
  link         for a remote engine, the time from the previous engine's
               result (or from publishing, for a first engine) to the
               client receiving this engine's result, less the time at the
               remote server: uplink and back together, plus the wait for a
               token for a first engine.

Age of information (AoI), per engine: at each moment, the age of the input
behind the newest result the app has: its time average, and its peaks just
before each new result. A remote engine's result counts from when the client
received it.

Clocks: when the bench runs the remote server, both servers share this
machine's clock. A remote server elsewhere (`addr` in the run's [remote])
stamps its events with its own clock, which no one syncs to the device's.
Then a time from the remote server is only compared with another from it
(service time, edge wait, idle), and the device's view of a remote event is
when the client received it (link, AoI, and the run's start and end). The
waits that need both clocks (age, excess, uplink, back, and before at a
remote first engine) are left out for remote engines.

Per producer, by Little's law: the average number of its inputs in flight,
from reaching the server to their last event, against its tokens.

A result and the offers it makes share one timestamp, so an offer's maker is
the result whose time equals the offer's.
"""

import argparse
import tomllib
from collections import defaultdict
from pathlib import Path

import pandas as pd

NS_PER_MS = 1e6
NS_PER_S = 1e9
MS_PER_S = 1e3
PERCENTILES = [0.5, 0.9, 0.99]
INPUT = ["producer", "session", "seq", "flow"]
# The events that end an offer at an engine.
ENDS = ["Result", "Skipped", "TimedOut"]
WAITS = ["age", "excess", "before", "uplink", "edge_wait", "idle", "back", "link"]
# The waits of a remote engine that subtract a time on one clock from a time
# on the other, when the remote server's clock is its own.
CROSS_CLOCK_WAITS = ["age", "excess", "before", "uplink", "back"]
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
    # Runs recorded before the remote server have no receipt stamps.
    if "received_ns" not in events:
        events["received_ns"] = float("nan")
    spec = tomllib.loads((run / "run.toml").read_text())
    # When the device knew of each event: a remote server's events reach it
    # when the client receives them.
    events["known_ns"] = events.received_ns.fillna(events.at_ns)
    return inputs, events, spec


def two_clocks(spec: dict) -> bool:
    """Whether the remote server is elsewhere, with its own clock."""
    return "addr" in spec.get("remote", {})


def remote_engines(spec: dict) -> set[str]:
    """The engines on the remote server."""
    return {
        engine["name"]
        for engine in spec["flow"]["engines"]
        if engine["location"] == "remote"
    }


def offers(
    inputs: pd.DataFrame, events: pd.DataFrame, spec: dict
) -> pd.DataFrame:
    """One row per offer sent to an engine: how it ended, and the waits."""
    sent = events[events.kind == "Sent"]
    ended = events[events.kind.isin(ENDS)][
        [*INPUT, "engine", "offered_ns", "kind", "at_ns", "received_ns"]
    ]
    offers = (
        sent.rename(columns={"received_ns": "sent_received_ns"})
        .merge(
            ended.rename(
                columns={
                    "kind": "end",
                    "at_ns": "ended_ns",
                    "received_ns": "ended_received_ns",
                }
            ),
            on=[*INPUT, "engine", "offered_ns"],
            how="left",
        )
        .merge(inputs, on=["producer", "session", "seq"], how="left")
    )
    offers["sent_ns"] = offers.at_ns
    offers["age"] = offers.sent_ns - offers.published_ns
    offers["edge_wait"] = offers.sent_ns - offers.offered_ns
    offers["service"] = (offers.ended_ns - offers.sent_ns).where(
        offers.end == "Result"
    )
    offers["back"] = offers.ended_received_ns - offers.ended_ns
    # When the app learned how the offer ended.
    offers["seen_ns"] = offers.ended_received_ns.fillna(offers.ended_ns)
    # When the device knew the input was sent to the engine.
    offers["sent_known_ns"] = offers.sent_received_ns.fillna(offers.sent_ns)
    remote = offers.engine.isin(remote_engines(spec))
    separate = two_clocks(spec)

    # Walk each offer back to the result that made it, in the order offers
    # were made, so a parent's waiting is known before its children's. On
    # one server, a result and the offers it makes share one timestamp. An
    # offer on another server is made later, by the client, so its parent is
    # the latest result before it from an engine with an edge to its engine.
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
    feeds = defaultdict(set)
    for edge in spec["flow"]["edges"]:
        feeds[edge["to"]].add(edge["from"])
    results = defaultdict(list)
    for index, row in offers[offers.end == "Result"].iterrows():
        results[tuple(row[INPUT])].append((row.ended_ns, row.engine, index))
    excess, before, uplink, link = {}, {}, {}, {}
    # Parents first: on one clock in the order offers were made, and with two
    # clocks in the order the device knew of them.
    order = "sent_known_ns" if separate else "offered_ns"
    for index, row in offers.sort_values(order).iterrows():
        key = tuple(row[INPUT])
        parent = made_by.get((*key, row.offered_ns))
        if parent is not None:
            excess[index] = excess[parent] + row.edge_wait
            continue
        # The offer was made on another server, after the latest result
        # before it from an engine with an edge to this one. With two clocks,
        # the device only knows that the result came before it heard of the
        # send.
        made_at = row.sent_known_ns if separate else row.offered_ns
        earlier = [
            (ended_ns, parent)
            for ended_ns, engine, parent in results[key]
            if engine in feeds[row.engine] and ended_ns <= made_at
        ]
        if earlier:
            ended_ns, parent = max(earlier)
            uplink[index] = row.offered_ns - ended_ns
            excess[index] = excess[parent] + uplink[index] + row.edge_wait
        else:
            ended_ns = row.published_ns
            before[index] = row.offered_ns - row.published_ns
            excess[index] = before[index] + row.edge_wait
        # Both on the device's clock, less a time measured on the remote
        # server's.
        link[index] = (
            row.ended_received_ns - ended_ns - (row.ended_ns - row.offered_ns)
        )
    # Typed, so a wait no offer has, such as the uplink on one server, is
    # still a column of numbers.
    offers["before"] = pd.Series(before, dtype=float)
    offers["uplink"] = pd.Series(uplink, dtype=float)
    offers["excess"] = pd.Series(excess, dtype=float)
    offers["link"] = pd.Series(link, dtype=float)
    if separate:
        offers.loc[remote, CROSS_CLOCK_WAITS] = float("nan")

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
        events.engine.notna() & events.known_ns.between(start, end)
    ]
    sent = offers[offers.sent_known_ns.between(start, end)]
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
    """How long the engine worked between `start` and `end`, on the
    device's clock. A remote engine's work is counted whole if the device
    knew it was sent in between."""
    if offers.sent_received_ns.isna().all():
        worked = offers.ended_ns.clip(upper=end) - offers.sent_ns.clip(lower=start)
    else:
        worked = offers.service.where(offers.sent_known_ns.between(start, end))
    return worked.where(offers.end == "Result").clip(lower=0).sum()


def waits(offers: pd.DataFrame) -> pd.DataFrame:
    """Per engine: percentiles of each wait, in ms."""
    in_ms = offers.set_index("engine")[WAITS] / NS_PER_MS
    return in_ms.groupby("engine").quantile(PERCENTILES).unstack()


def age_of_information(
    offers: pd.DataFrame, start: float, end: float
) -> pd.DataFrame:
    """Per engine: the time-average AoI of its results, and its peaks, in ms.

    A local result counts from when its server got it; the app hears of it a
    few microseconds later. A remote result counts from when the client
    received it.
    """
    rows = {}
    results = offers[offers.end == "Result"]
    for engine, group in results.groupby("engine"):
        group = group.sort_values("seen_ns")
        times = group.seen_ns.to_numpy()
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
    # On the device's clock: an offer at a remote server counts from when the
    # device heard of it.
    offered = timed.offered_ns.where(timed.received_ns.isna(), timed.received_ns)
    by_input = ["producer", "session", "seq"]
    first = offered.groupby([timed[c] for c in by_input]).min()
    last = timed.groupby(by_input).known_ns.max()
    spans = pd.DataFrame({"start": first, "end": last}).dropna()
    # A spec without a count leaves it to the client ("Choosing C").
    tokens = {
        p["name"]: p.get("tokens", "computed")
        for p in spec["flow"]["producers"]
    }
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
    all_offers = offers(inputs, events, spec)
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
