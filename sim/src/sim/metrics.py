"""Result sink and experiment metrics.

Definitions (CLAUDE.md "Evaluating the design"):
    throughput       results per second reaching the sink, per leaf
    latency          result arrival - timestamp of its oldest input sample
    age of info      time average of (now - sample time of the newest result
                     held), per leaf
    wasted run       a run whose output no child consumed (taken into a run;
                     for leaves, delivered to the sink). `unsent_runs` uses
                     the narrower protocol definition: output never sent.
    wasted transfer  a frame that crossed the network and was overwritten in
                     the receiver's slot before use
    slot wait / idle per input / per run, from the engine nodes
    lease flaps      lease expiry followed shortly by a demand on that edge
"""

from collections import defaultdict
from statistics import fmean

from . import messages as m
from .estimators import percentile
from .flow import SOURCE, TOKEN_MODES, FlowSpec
from .messages import Item


class Sink:
    """Consumer of leaf results, living with the Sources (same clock)."""

    def __init__(self, spec: FlowSpec, now, send_control):
        self.spec = spec
        self.now = now
        self.send_control = send_control
        self.results: dict[str, list[tuple[float, float, float, int, bool]]] = defaultdict(list)

    def on_result(self, leaf: str, item: Item):
        self.results[leaf].append(
            (self.now(), item.oldest, item.seq, item.producer[1], bool(item.meta.get("partial")))
        )
        if self.spec.mode in TOKEN_MODES:
            by_source = defaultdict(list)
            for src, sid in item.samples():
                by_source[src].append([src, sid])
            for src, ids in by_source.items():
                self.send_control({
                    "t": m.TOKENS, "f": self.spec.name, "frm": leaf, "to": src,
                    "samples": ids, "why": "done",
                })

    def consumed(self) -> dict[str, set[int]]:
        return {leaf: {r[3] for r in rs} for leaf, rs in self.results.items()}


def age_of_information(results, t0: float, t1: float) -> float | None:
    """Time-averaged age of the newest result held over [t0, t1].
    results: (arrival, sample_ts) pairs. Averaging starts at t0, or at the
    first result if none had arrived by then."""
    held = None
    prev = None
    area = 0.0
    start = None

    def segment(a, b):
        nonlocal area, start
        a = max(a, t0)
        if b > a:
            area += (b - a) * ((a + b) / 2 - held)
            start = a if start is None else start

    for arrival, ts in sorted(results):
        if arrival > t1:
            break
        if held is not None:
            segment(prev, arrival)
        held = ts if held is None else max(held, ts)
        prev = arrival
    if held is None:
        return None
    segment(prev, t1)
    if start is None:
        return None
    return area / (t1 - start)


def _stats(values) -> dict:
    if not values:
        return {"mean": None, "p50": None, "p95": None}
    return {"mean": fmean(values), "p50": percentile(values, 50), "p95": percentile(values, 95)}


def summarize(spec: FlowSpec, sink: Sink, reports: dict[str, dict], t0: float, t1: float) -> dict:
    """Combine the sink's records (client clock, window [t0, t1]) with every
    node's report (each already cut to its own host's window)."""
    window = t1 - t0
    leaves = spec.leaves()
    per_leaf = {}
    latencies = []
    for leaf in leaves:
        rs = [r for r in sink.results.get(leaf, []) if t0 <= r[0] <= t1]
        lat = [r[0] - r[1] for r in rs]
        latencies += lat
        aoi = age_of_information([(r[0], r[1]) for r in sink.results.get(leaf, [])], t0, t1)
        per_leaf[leaf] = {
            "throughput": len(rs) / window if window > 0 else 0.0,
            "latency": _stats(lat),
            "aoi": aoi,
            "partial": sum(1 for r in rs if r[4]),
        }

    # Which outputs of each node were consumed by some child (or the sink).
    consumed: dict[str, set[int]] = defaultdict(set)
    for name, rep in reports.items():
        for parent, ids in rep.get("consumed", {}).items():
            consumed[parent].update(ids)
    for leaf, ids in sink.consumed().items():
        consumed[leaf].update(ids)

    nodes = {}
    tot = defaultdict(float)
    for n in spec.nodes:
        rep = reports.get(n.name)
        if rep is None:
            nodes[n.name] = {"missing": True}
            continue
        if n.kind == SOURCE:
            nodes[n.name] = {
                "frames": rep["frames"], "sends": rep["sends"],
                "lease_expiries": rep["lease_expiries"], "flaps": rep["flaps"],
                "tokens_reclaimed": rep["tokens_reclaimed"],
                "token_window": rep.get("token_window"),
                "token_returns": rep.get("token_returns"),
            }
            tot["lease_expiries"] += rep["lease_expiries"]
            tot["flaps"] += rep["flaps"]
            continue
        outputs = rep["outputs"]
        wasted = sum(1 for i in outputs if i not in consumed[n.name])
        unsent = sum(1 for i in outputs if i not in set(rep["sent"]))
        w = rep["window"] or window
        nodes[n.name] = {
            "runs": rep["runs"],
            "rejects": rep["rejects"],
            "partial": rep["partial"],
            "wasted_runs": wasted,
            "unsent_runs": unsent,
            "utilization": rep["busy"] / w if w else None,
            "idle_frac": sum(rep["idle"]) / w if w else None,
            "slot_wait": _stats(rep["slot_wait"]),
            "overwrites": sum(rep["overwrites"].values()),
            "wasted_transfers": rep["wasted_transfers"],
            "lease_expiries": rep["lease_expiries"],
            "flaps": rep["flaps"],
            "r_p50_ms": rep.get("r_p50", 0) * 1000,
        }
        for k in ("runs", "rejects", "wasted_runs", "unsent_runs", "overwrites",
                  "wasted_transfers", "lease_expiries", "flaps"):
            tot[k] += nodes[n.name][k]

    def mean_of(key):
        vals = [v[key] for v in per_leaf.values() if v[key] is not None]
        return fmean(vals) if vals else None

    timeline = {
        leaf: [[r[0] - t0, r[0] - r[1], int(r[4])] for r in sink.results.get(leaf, [])
               if t0 - 1 <= r[0] <= t1]
        for leaf in leaves
    }
    return {
        "flow": spec.name,
        "mode": spec.mode,
        "timeline": timeline,  # per leaf: [time since window start, latency, partial]
        "window": window,
        "throughput": mean_of("throughput"),
        "latency": _stats(latencies),
        "aoi": mean_of("aoi"),
        "runs": int(tot["runs"]),
        "wasted_runs": int(tot["wasted_runs"]),
        "wasted_frac": tot["wasted_runs"] / tot["runs"] if tot["runs"] else 0.0,
        "unsent_runs": int(tot["unsent_runs"]),
        "rejects": int(tot["rejects"]),
        "overwrites": int(tot["overwrites"]),
        "wasted_transfers": int(tot["wasted_transfers"]),
        "lease_expiries": int(tot["lease_expiries"]),
        "flaps": int(tot["flaps"]),
        "leaves": per_leaf,
        "nodes": nodes,
    }


def node_report(node, t0: float, t1: float) -> dict:
    rep = node.metrics.report(t0, t1)
    r = getattr(node, "r", None)
    rep["r_p50"] = r.p50 if r is not None else 0.0
    return rep
