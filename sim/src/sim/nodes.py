"""Per-node scheduling state machines.

Each node is ONE event-driven state machine (events: data arrival, control
arrival, engine finished, timers), as protocol.txt asks. Nodes never touch a
transport or the clock directly; they go through a `NodeIO`, so the same code
runs under asyncio (servers and client) and under a virtual clock (tests).

Modes (flow.MODES):
    schedule  protocol v1 (protocol.txt): demand schedules with interval,
              phase and lease; Source timer-driven, interior output-driven
    push      Sources publish every frame; engines forward every output
    pull      one-ahead pull: an engine requests one input from its parents
              when it starts a run (leaves) or when a child is waiting
    token     CONWIP: push, but each Source holds N tokens; a token returns
              when its sample reaches the sink or is dropped mid-graph
    token-adaptive
              token, but each Source adjusts N once per token round trip:
              +1 when all tokens are out and nothing was overwritten, -1 on
              an overwrite or a reclaimed (lost) token
    credit    hop-by-hop credits: each child grants each parent a window of
              sends, returns a credit when it takes an input, and adapts the
              window per edge (+1 when it idled because the window ran out,
              -1 on an overwrite or a missing frame)
    schedule-hop
              credit, but each credit is sent at the time it's needed: when
              this node will be free and its fastest child ready, minus its
              run time and a per-edge lead learned from arrival errors
    segment   fixed token counts at fan-out points: a Source, or an engine
              with several children, splits its children into a local and a
              remote group, each with its own count; a remote group sends
              one copy across the link for all its children; engines in
              between just push
"""

import math
import random
from collections import defaultdict, deque
from statistics import median
from dataclasses import dataclass
from typing import Callable, Protocol

from . import messages as m
from .estimators import Period, RunTime
from .flow import (
    CREDIT, PULL, PUSH, SCHEDULE, SCHEDULE_HOP, SEGMENT, SINK, SOURCE, TOKEN, TOKEN_ADAPTIVE,
    FlowSpec,
)
from .messages import Item, merge_lineage

INF = math.inf


class Timer(Protocol):
    def cancel(self) -> None: ...


class NodeIO(Protocol):
    """What a node needs from its host."""

    def now(self) -> float: ...

    def call_at(self, when: float, callback: Callable[[], None]) -> Timer: ...

    def send_data(self, frm: str, to: str, item: Item) -> None:
        """Send an item on edge frm -> to (to may be SINK)."""

    def send_control(self, msg: dict) -> None:
        """Send a control message to node msg["to"] of the same flow."""

    def run_engine(self, node: str, inputs: list[Item], done: Callable[[bytes | None], None]) -> None:
        """Run the node's engine; call done(output or None) when it finishes."""


def calc_lease(c: float, r: float, w: float) -> float:
    return c * max(r, w)


def next_on_grid(t: float, i: float, now: float) -> float:
    """First point of the grid t, t + i, t + 2i, ... that is not in the past.
    Skips missed points instead of bursting, keeping the original phase."""
    if i <= 0:
        return max(t, now)
    if t >= now:
        return t
    k = math.ceil((now - t) / i)
    return t + k * i


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


class NodeMetrics:
    """Raw per-node measurements, timestamped on the node's own clock so a
    report can cut them to the measurement window [mark, stop]."""

    def __init__(self):
        self.runs: list[tuple[int, float, float, bool, bool]] = []  # id, start, end, output, partial
        self.sent: set[int] = set()  # run ids whose output went out on some edge
        self.consumed: dict[str, set[int]] = defaultdict(set)  # parent -> producer run ids
        self.slot_wait: list[tuple[float, float]] = []
        self.idle: list[tuple[float, float]] = []
        self.overwrites: dict[str, list[float]] = defaultdict(list)
        self.wasted_transfers: list[float] = []
        self.lease_expiries: list[float] = []
        self.flaps: list[float] = []
        self.demands_in: list[float] = []
        self.sends: dict[str, list[float]] = defaultdict(list)
        self.frames: list[float] = []
        self.tokens_reclaimed: list[float] = []
        self.token_window: list[tuple[float, int]] = []  # (time, N) on every change
        self.token_returns: dict[str, list[float]] = defaultdict(list)  # why -> times
        self.credit_window: dict[str, list[tuple[float, int]]] = defaultdict(list)  # parent -> (t, W)
        self.unused: dict[str, list[float]] = defaultdict(list)  # segment: group -> frames nobody used

    def report(self, t0: float, t1: float) -> dict:
        def window(xs):
            return [x for x in xs if t0 <= x <= t1]

        def window_v(pairs):
            return [v for t, v in pairs if t0 <= t <= t1]

        runs = [r for r in self.runs if t0 <= r[2] <= t1]
        return {
            "runs": len(runs),
            "rejects": sum(1 for r in runs if not r[3]),
            "partial": sum(1 for r in runs if r[4]),
            "outputs": [r[0] for r in runs if r[3]],
            "busy": sum(r[2] - r[1] for r in runs),
            "sent": sorted(self.sent),
            "consumed": {p: sorted(ids) for p, ids in self.consumed.items()},
            "slot_wait": window_v(self.slot_wait),
            "idle": window_v(self.idle),
            "overwrites": {p: len(window(ts)) for p, ts in self.overwrites.items()},
            "wasted_transfers": len(window(self.wasted_transfers)),
            "lease_expiries": len(window(self.lease_expiries)),
            "flaps": len(window(self.flaps)),
            "demands_in": len(window(self.demands_in)),
            "sends": {c: len(window(ts)) for c, ts in self.sends.items()},
            "frames": len(window(self.frames)),
            "tokens_reclaimed": len(window(self.tokens_reclaimed)),
            "token_window": _time_average(self.token_window, t0, t1),
            "token_returns": {why: len(window(ts)) for why, ts in self.token_returns.items()},
            "credit_window": {p: _time_average(steps, t0, t1) for p, steps in self.credit_window.items()},
            "unused": {g: len(window(ts)) for g, ts in self.unused.items()},
            "window": t1 - t0,
        }


# ---------------------------------------------------------------------------
# base classes
# ---------------------------------------------------------------------------


def _time_average(steps: list[tuple[float, float]], t0: float, t1: float) -> float | None:
    """Time average over [t0, t1] of a step function given as (time, value)."""
    if not steps or t1 <= t0:
        return None
    total, value = 0.0, None
    prev = t0
    for t, v in steps:
        if t <= t0:
            value = v
            continue
        if t >= t1:
            break
        if value is not None:
            total += value * (t - prev)
        prev, value = t, v
    if value is None:
        return None
    total += value * (t1 - prev)
    return total / (t1 - max(t0, steps[0][0]))


class Node:
    def __init__(self, spec: FlowSpec, name: str, io: NodeIO):
        self.spec = spec
        self.flow = spec.name
        self.name = name
        self.ns = spec.node(name)
        self.p = spec.params
        self.io = io
        self.parents = spec.parents(name)
        self.children = spec.children(name)
        self.metrics = NodeMetrics()
        self.stopped = False
        self._timers: set = set()
        self.w = self.p.w_ms / 1000
        self.eps = self.p.eps_ms / 1000

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def now(self) -> float:
        return self.io.now()

    def timer(self, when: float, callback: Callable[[], None]):
        holder = []

        def fire():
            self._timers.discard(holder[0])
            if not self.stopped:
                callback()

        handle = self.io.call_at(when, fire)
        holder.append(handle)
        self._timers.add(handle)
        return handle

    def cancel(self, handle):
        if handle is not None:
            handle.cancel()
            self._timers.discard(handle)

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        for handle in list(self._timers):
            handle.cancel()
        self._timers.clear()

    def send(self, to: str, item: Item):
        self.metrics.sends[to].append(self.now())
        if self.ns.kind != SOURCE:
            self.metrics.sent.add(item.producer[1])
        self.io.send_data(self.name, to, item)

    def control(self, msg_type: str, to: str, **fields):
        self.io.send_control({"t": msg_type, "f": self.flow, "frm": self.name, "to": to, **fields})

    def on_data(self, frm: str, item: Item):
        raise NotImplementedError(f"{type(self).__name__} takes no data")

    def on_control(self, msg: dict):
        pass

    def on_link(self, up: bool):
        """The client <-> remote link went down or came back."""


class SourceNode(Node):
    """A Source gains a frame every period P and decides when to publish."""

    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self.period = self.ns.period_ms / 1000
        self.p_est = Period(self.period)
        self.payload = bytes(self.ns.frame_bytes)
        self.newest: Item | None = None
        self.sample_id = 0
        self._t0 = None
        self._rng = random.Random(f"{spec.seed}/{spec.name}/{name}")

    def start(self):
        self._t0 = self.now()
        self._next_frame()

    def _next_frame(self):
        k = self.sample_id
        when = self._t0 + k * self.period
        if self.ns.jitter_ms:
            when += self._rng.uniform(-1, 1) * self.ns.jitter_ms / 1000
        self.timer(max(when, self.now()), self._frame)

    def _frame(self):
        now = self.now()
        self.sample_id += 1
        self.p_est.arrival(now)
        self.metrics.frames.append(now)
        self.newest = Item(
            lineage=[(self.name, self.sample_id, now)],
            producer=(self.name, self.sample_id),
            payload=self.payload,
        )
        self.on_frame(self.newest)
        self._next_frame()

    def on_frame(self, item: Item):
        pass


class EngineNode(Node):
    """Input handling shared by every mode: one newest-wins slot per parent
    (a single-parent node is a join with one parent), grace timer, run, R."""

    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        seed = self.ns.r_seed_ms if self.ns.r_seed_ms is not None else self.p.r_seed_ms
        self.r = RunTime(seed / 1000, self.p.r_window)
        self.g = None if self.ns.grace_ms is None else self.ns.grace_ms / 1000
        self.slots: dict[str, Item | None] = {p: None for p in self.parents}
        self.arrived: dict[str, float] = {}
        self.seq: dict[str, float] = {p: 0 for p in self.parents}
        self.first: float | None = None
        self._grace = None
        self.running = False
        self.start_t = -INF
        self.end_t = -INF
        self.run_id = 0
        self._network_parent = {p: spec.is_network_edge(p, name) for p in self.parents}

    # -- inputs ------------------------------------------------------------

    def on_data(self, frm: str, item: Item):
        if self.stopped or frm not in self.slots:
            return
        now = self.now()
        old = self.slots[frm]
        if old is not None:
            self.metrics.overwrites[frm].append(now)
            # A copy sent once to several consumers (segment mode, "mc")
            # isn't wasted when one of them skips it; its head counts it if
            # nobody used it.
            if self._network_parent[frm] and old.meta.get("mc", 1) <= 1:
                self.metrics.wasted_transfers.append(now)
            self.on_drop(old)
        self.slots[frm] = item
        self.arrived[frm] = now
        self.seq[frm] = max(self.seq[frm], item.seq)
        self.input_arrived(frm)
        if self.first is None:
            self.first = now
            if self.g is not None and len(self.parents) > 1:
                self._grace = self.timer(now + self.g, self._maybe_run)
        self._maybe_run()

    def _ready(self) -> bool:
        if all(v is not None for v in self.slots.values()):
            return True
        if self.g is None or self.first is None:
            return False
        return self.first + self.g <= self.now() + 1e-9

    def _maybe_run(self):
        if self.running or self.stopped:
            return
        if not any(v is not None for v in self.slots.values()) or not self._ready():
            return
        self._run()

    def _run(self):
        now = self.now()
        taken = {p: v for p, v in self.slots.items() if v is not None}
        for p in taken:
            self.slots[p] = None
        partial = len(taken) < len(self.parents)
        first_arrival = min(self.arrived[p] for p in taken)
        self.metrics.idle.append((now, max(0.0, first_arrival - self.end_t) if self.end_t > -INF else 0.0))
        for p, item in taken.items():
            self.metrics.slot_wait.append((now, now - self.arrived[p]))
            self.metrics.consumed[p].add(item.producer[1])
        self.first = None
        self.cancel(self._grace)
        self._grace = None
        self.run_id += 1
        self.running = True
        self.start_t = now
        self._partial = partial
        self._missing = [p for p in self.parents if p not in taken]
        self._inputs = [taken[p] for p in self.parents if p in taken]
        self.on_run_start(taken)
        self.io.run_engine(self.name, self._inputs, self._finished)

    def _finished(self, payload: bytes | None):
        if self.stopped:
            return
        now = self.now()
        self.end_t = now
        self.r.update(now - self.start_t)
        self.running = False
        has_output = payload is not None
        self.metrics.runs.append((self.run_id, self.start_t, now, has_output, self._partial))
        if has_output:
            meta = {"partial": self._missing} if self._missing else {}
            item = Item(
                lineage=merge_lineage(self._inputs),
                producer=(self.name, self.run_id),
                payload=payload,
                meta=meta,
            )
            if self.is_leaf:
                self.send(SINK, item)
            else:
                self.on_output(item)
        else:
            self.on_reject(self._inputs)
        self._inputs = []
        self._maybe_run()
        self.after_run()

    # -- hooks ---------------------------------------------------------------

    def input_arrived(self, frm: str):
        pass

    def on_run_start(self, taken: dict[str, Item]):
        pass

    def on_output(self, item: Item):
        pass

    def on_reject(self, inputs: list[Item]):
        pass

    def on_drop(self, item: Item):
        pass

    def after_run(self):
        pass


# ---------------------------------------------------------------------------
# schedule mode (protocol v1)
# ---------------------------------------------------------------------------


@dataclass
class EdgeState:
    """Per child edge state (protocol.txt `dc`)."""

    exp: float
    i: float
    next: float  # Source: next send time; interior: earliest next send
    last_seq: float
    best: float
    worst: float
    phase: float
    received: float
    last_sent: int = -1  # producer id (run id or sample id) of the last send
    last_send_t: float = -INF
    pending: Item | None = None  # interior: output waiting for this edge's slot
    waiting: bool = False  # Source: slot came before a new frame
    send_timer: object = None
    lease_timer: object = None


class _ScheduleParent:
    """Child-edge bookkeeping shared by the schedule Source and engines."""

    phase_timed = False  # True for the Source

    def _init_parent(self):
        self.dc: dict[str, EdgeState] = {}
        self.expired_at: dict[str, float] = {}

    def _live(self, now: float) -> dict[str, EdgeState]:
        return {c: d for c, d in self.dc.items() if d.exp > now}

    def _accept_demand(self, msg: dict, min_interval: float = 0.0) -> EdgeState | None:
        now = self.now()
        c = msg["frm"]
        d = self.dc.get(c)
        if d is not None and msg["seq"] != 0 and msg["seq"] < d.last_seq:
            return None  # stale (reordered) demand
        self.metrics.demands_in.append(now)
        if d is None and c in self.expired_at:
            if now - self.expired_at.pop(c) <= self.p.flap_window_ms / 1000:
                self.metrics.flaps.append(now)
        i = max(msg["best"], msg["worst"], min_interval)
        if d is None or d.exp <= now:
            nxt = now  # edge was not active: send immediately
        elif not self.phase_timed:
            # Interior: output-driven; `next` is the earliest next send.
            nxt = d.last_send_t + i
        else:
            # Source: the only place Phase sets a send time. A renewal that
            # carries no new data (same Seq: idle heartbeats, or recomputed
            # demands with nothing new below) keeps the current schedule.
            # Re-anchoring on those starves the edge whenever the wrapped
            # phase is longer than the renewal period: each renewal pushes
            # the next send out again before it fires.
            # A re-anchored send is held until I - eps after the previous
            # one (principle 5), so demands arriving faster than I can't
            # raise the send rate.
            if msg["seq"] == d.last_seq:
                nxt = next_on_grid(d.next, i, now)
            else:
                nxt = max(next_on_grid(now + msg["phase"], i, now), d.last_send_t + i - self.eps)
        new = EdgeState(
            exp=now + msg["lease"], i=i, next=nxt, last_seq=msg["seq"],
            best=msg["best"], worst=msg["worst"], phase=msg["phase"], received=now,
        )
        if d is not None:
            new.last_sent = d.last_sent
            new.last_send_t = d.last_send_t
            new.pending = d.pending
            new.waiting = d.waiting
            self.cancel(d.send_timer)
            self.cancel(d.lease_timer)
        new.lease_timer = self.timer(new.exp, lambda c=c: self._lease_check(c))
        self.dc[c] = new
        return new

    def _drop_edge(self, c: str, expired: bool):
        d = self.dc.pop(c, None)
        if d is None:
            return
        self.cancel(d.send_timer)
        self.cancel(d.lease_timer)
        if expired:
            now = self.now()
            self.metrics.lease_expiries.append(now)
            self.expired_at[c] = now
        self._edge_gone(c)

    def _lease_check(self, c: str):
        d = self.dc.get(c)
        if d is not None and d.exp <= self.now() + 1e-9:
            self._drop_edge(c, expired=True)

    def _edge_gone(self, c: str):
        pass


class ScheduleSource(_ScheduleParent, SourceNode):
    """Timer-driven: the only place Phase sets a send time."""

    phase_timed = True

    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_parent()
        self._tick_timer = None

    def on_control(self, msg):
        if msg["t"] == m.DEMAND:
            if self._accept_demand(msg, min_interval=self.p_est.value) is not None:
                self._arm()
        elif msg["t"] == m.TERM:
            self._drop_edge(msg["frm"], expired=False)

    def _arm(self):
        self.cancel(self._tick_timer)
        live = self._live(self.now())
        if live:
            self._tick_timer = self.timer(min(d.next for d in live.values()), self._tick)

    def _tick(self):
        self._tick_timer = None
        now = self.now()
        for c, d in self._live(now).items():
            if d.next <= now + 1e-9:
                f = self.newest
                if f is not None and f.producer[1] > d.last_sent:
                    self.send(c, f)
                    d.last_sent = f.producer[1]
                    d.last_send_t = now
                    d.waiting = False
                else:
                    d.waiting = True  # send the next frame as soon as it exists
                d.next = next_on_grid(d.next + d.i, d.i, now)
        self._arm()

    def on_frame(self, item):
        for c, d in self._live(self.now()).items():
            if d.waiting and item.producer[1] > d.last_sent:
                self.send(c, item)
                d.last_sent = item.producer[1]
                d.last_send_t = self.now()
                d.waiting = False


class ScheduleEngine(_ScheduleParent, EngineNode):
    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_parent()
        self.last_cstar: str | None = None
        self.last_live: frozenset = frozenset()
        self.last_idle_demand = -INF
        self._idle_timer = None
        self.i = None

    # -- leaf: backward propagation --------------------------------------

    def start(self):
        if self.is_leaf:
            self._idle_demand()

    def _demand_up(self, best, worst, phase, lease):
        for p in self.parents:
            self.control(m.DEMAND, p, best=best, worst=worst, phase=phase,
                         seq=self.seq[p], lease=lease)

    @property
    def pace(self) -> float:
        """Run time used for Best/Worst (the committed pace); leases keep p80."""
        return self.r.pct(self.p.pace_pct)

    def _idle_demand(self):
        r = self.pace
        self._demand_up(r, r, 0.0, calc_lease(self.p.c, self.r.p80, self.w))
        self.last_idle_demand = self.now()
        self._arm_idle()

    def _arm_idle(self):
        self.cancel(self._idle_timer)
        when = max(self.end_t, self.last_idle_demand) + self.w
        self._idle_timer = self.timer(when, self._idle_tick)

    def _idle_tick(self):
        self._idle_timer = None
        if self.running:
            return  # re-armed when the run finishes
        if self.now() >= max(self.end_t, self.last_idle_demand) + self.w - 1e-9:
            self._idle_demand()
        else:
            self._arm_idle()

    def on_run_start(self, taken):
        if self.is_leaf:
            r = self.pace
            self._demand_up(r, r, self.r.p20, calc_lease(self.p.c, self.r.p80, self.w))

    def after_run(self):
        if self.is_leaf and not self.running:
            self._arm_idle()

    # -- interior: demands from children ------------------------------------

    def on_control(self, msg):
        if msg["t"] == m.DEMAND:
            d = self._accept_demand(msg)
            if d is None:
                return
            if d.pending is not None:
                self._try_send(msg["frm"])
            self._maybe_send_up(trigger=msg["frm"])
        elif msg["t"] == m.TERM:
            self._drop_edge(msg["frm"], expired=False)

    def _edge_gone(self, c):
        if not self._live(self.now()):
            for p in self.parents:
                self.control(m.TERM, p)
            self.last_cstar = None
            self.last_live = frozenset()
        else:
            self._maybe_send_up(trigger=c)

    def _maybe_send_up(self, trigger: str):
        now = self.now()
        live = self._live(now)
        if not live:
            return
        cstar = min(live, key=lambda c: (max(live[c].best, live[c].worst), c))
        live_set = frozenset(live)
        if not (trigger == cstar or cstar != self.last_cstar or live_set != self.last_live):
            return
        self.last_cstar, self.last_live = cstar, live_set
        d = live[cstar]
        # A stored phase is a duration from its receipt: age it.
        best, worst = d.best, max(d.worst, self.pace)
        ph = next_on_grid(d.received + d.phase, d.i, now) - now
        self.i = max(best, worst)
        self._demand_up(best, worst, ph - self.r.p50, calc_lease(self.p.c, self.r.p80, self.w))

    # -- interior: forward propagation (output-driven) -----------------------

    def on_output(self, item):
        # Send on each leased edge whose last send was at least I - eps ago;
        # otherwise hold it in that edge's slot (newer replaces older). The
        # Source's interval alone can't pace a fan-out: it follows the
        # fastest child, so slower children need their own per-edge interval.
        now = self.now()
        for c, d in self._live(now).items():
            d.pending = item
            self._try_send(c)

    def _try_send(self, c: str):
        d = self.dc.get(c)
        if d is None or d.pending is None:
            return
        now = self.now()
        if d.pending.producer[1] <= d.last_sent:
            d.pending = None  # never send the same output twice on an edge
            return
        if d.next - self.eps <= now:
            self.cancel(d.send_timer)
            d.send_timer = None
            self.send(c, d.pending)
            d.last_sent = d.pending.producer[1]
            d.last_send_t = now
            d.pending = None
            d.next = now + d.i
        else:
            self.cancel(d.send_timer)
            d.send_timer = self.timer(d.next - self.eps, lambda c=c: self._try_send(c))


# ---------------------------------------------------------------------------
# push mode
# ---------------------------------------------------------------------------


class PushSource(SourceNode):
    def on_frame(self, item):
        for c in self.children:
            self.send(c, item)


class PushEngine(EngineNode):
    def on_output(self, item):
        for c in self.children:
            self.send(c, item)


# ---------------------------------------------------------------------------
# pull mode (one-ahead pull from the next engine)
# ---------------------------------------------------------------------------


class _PullParent:
    def _init_parent(self):
        self.wanting: set[str] = set()
        self.last_sent: dict[str, int] = {}
        self.latest: Item | None = None

    def _serve(self, c: str) -> bool:
        item = self.latest
        if item is not None and item.producer[1] > self.last_sent.get(c, -1):
            self.send(c, item)
            self.last_sent[c] = item.producer[1]
            return True
        return False

    def _serve_waiting(self):
        for c in list(self.wanting):
            if self._serve(c):
                self.wanting.discard(c)


class PullSource(_PullParent, SourceNode):
    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_parent()

    def on_control(self, msg):
        if msg["t"] == m.REQUEST and not self._serve(msg["frm"]):
            self.wanting.add(msg["frm"])

    def on_frame(self, item):
        self.latest = item
        self._serve_waiting()


class PullEngine(_PullParent, EngineNode):
    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_parent()
        self.outstanding: dict[str, float] = {}
        self.timeout = self.p.pull_timeout_ms / 1000

    def start(self):
        self._need()

    def _wants(self) -> bool:
        return self.is_leaf or bool(self.wanting)

    def _request(self, p: str):
        now = self.now()
        self.outstanding[p] = now
        self.control(m.REQUEST, p)
        self.timer(now + self.timeout, lambda p=p, t=now: self._timeout(p, t))

    def _timeout(self, p: str, sent: float):
        if self.outstanding.get(p) == sent:
            del self.outstanding[p]
            self._need()

    def _need(self):
        """Request inputs if someone downstream wants one and none is on its way."""
        if not self._wants() or self.running:
            return
        for p in self.parents:
            if self.slots[p] is None and p not in self.outstanding:
                self._request(p)

    def input_arrived(self, frm):
        self.outstanding.pop(frm, None)

    def on_run_start(self, taken):
        if self.is_leaf:  # one ahead: the next input arrives while this run runs
            for p in self.parents:
                if self.slots[p] is None and p not in self.outstanding:
                    self._request(p)

    def on_control(self, msg):
        if msg["t"] == m.REQUEST:
            if not self._serve(msg["frm"]):
                self.wanting.add(msg["frm"])
                self._need()

    def on_output(self, item):
        self.latest = item
        self._serve_waiting()

    def after_run(self):
        self._need()


# ---------------------------------------------------------------------------
# token mode (CONWIP)
# ---------------------------------------------------------------------------


def return_tokens(node: Node, samples: list[tuple[str, int]], why: str):
    """Send tokens back to their Sources. why: done | drop | reject."""
    by_source = defaultdict(list)
    for src, sid in samples:
        by_source[src].append([src, sid])
    for src, ids in by_source.items():
        node.control(m.TOKENS, src, samples=ids, why=why)


def tokens_for_replaced(spec: FlowSpec, chan_parent: str, raw: bytes) -> list[dict]:
    """Token returns for a frame replaced in a sender slot before it left.
    The transport drops it silently, so without this its token would only
    come back after token_timeout."""
    if spec.mode not in (TOKEN, TOKEN_ADAPTIVE):
        return []
    by_source = defaultdict(list)
    for src, sid in Item.decode(raw).samples():
        by_source[src].append([src, sid])
    return [
        {"t": m.TOKENS, "f": spec.name, "frm": chan_parent, "to": src, "samples": ids, "why": "drop"}
        for src, ids in by_source.items()
    ]


class TokenSource(SourceNode):
    """Admits a frame only while fewer than `window` tokens are out. A token
    returns when its sample reaches the sink or is dropped (filter
    rejection, overwrite), or after token_timeout if the return was lost."""

    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self.window = self.p.tokens
        self.outstanding: dict[int, float] = {}
        self.last_sent = -1
        self.timeout = self.p.token_timeout_ms / 1000

    @property
    def tokens(self) -> int:
        return self.window - len(self.outstanding)

    def start(self):
        super().start()
        self.metrics.token_window.append((self.now(), self.window))
        self._reclaim_tick()

    def _reclaim_tick(self):
        now = self.now()
        for sid, t in list(self.outstanding.items()):
            if now - t >= self.timeout:
                del self.outstanding[sid]
                self.metrics.tokens_reclaimed.append(now)
                self.metrics.token_returns["lost"].append(now)
                self.on_token_back("lost", None)
        self._release()
        self.timer(now + self.timeout / 4, self._reclaim_tick)

    def _release(self):
        f = self.newest
        if self.tokens > 0 and f is not None and f.producer[1] > self.last_sent:
            self.outstanding[f.producer[1]] = self.now()
            self.last_sent = f.producer[1]
            for c in self.children:
                self.send(c, f)

    def on_frame(self, item):
        if self.tokens <= 0:
            self.on_blocked()
        self._release()

    def on_control(self, msg):
        if msg["t"] != m.TOKENS:
            return
        why = msg.get("why", "done")
        for src, sid in msg["samples"]:
            if src != self.name:
                continue
            sent = self.outstanding.pop(sid, None)
            if sent is not None:
                self.metrics.token_returns[why].append(self.now())
                self.on_token_back(why, self.now() - sent)
        self._release()

    # -- hooks for the adaptive variant ------------------------------------

    def on_token_back(self, why: str, round_trip: float | None):
        pass

    def on_blocked(self):
        pass


class AdaptiveTokenSource(TokenSource):
    """Token count adjusted like a congestion window, from signals the
    Source already has:

    - grow (+1) when a frame arrives while every token is out (the pipeline
      could take more) and no overwrite came back in the last round trip
    - shrink (-1) when a token returns because its frame was overwritten
      (too many in flight), or a lost token is reclaimed
    - filter rejections are neutral

    Each direction changes N at most once per token round trip (median of
    recent returns), so a burst of signals from one window counts once.
    """

    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self.window = max(1, self.p.tokens)
        self.rtts: deque[float] = deque(maxlen=16)
        self.last_up = -INF
        self.last_down = -INF

    @property
    def round_trip(self) -> float:
        return median(self.rtts) if self.rtts else self.period * self.window

    def _set(self, window: int):
        window = max(1, min(self.p.tokens_max, window))
        if window != self.window:
            self.window = window
            self.metrics.token_window.append((self.now(), window))

    def on_token_back(self, why, round_trip):
        now = self.now()
        if round_trip is not None and why == "done":
            self.rtts.append(round_trip)
        if why in ("drop", "lost") and now - self.last_down >= self.round_trip:
            self.last_down = now
            self._set(self.window - 1)

    def on_blocked(self):
        now = self.now()
        rt = self.round_trip
        if now - self.last_up >= rt and now - self.last_down >= rt:
            self.last_up = now
            self._set(self.window + 1)


class TokenEngine(PushEngine):
    def on_drop(self, item):
        return_tokens(self, item.samples(), "drop")

    def on_reject(self, inputs):
        return_tokens(self, [s for i in inputs for s in i.samples()], "reject")


# ---------------------------------------------------------------------------
# credit mode (hop-by-hop, per-edge adaptive windows)
# ---------------------------------------------------------------------------
#
# Counters are cumulative, so lost or reordered messages are harmless:
#   child -> parent  {"t": "credit", "g": G}  G = credits granted so far
#   parent -> child  each item carries meta["c"] = S, sends so far on the edge,
#                    meta["h"] = 1 if the edge ran out of credits since the
#                    previous send (the window, not the parent, was the
#                    limit), and meta["w"] = 1 if this output sat ready
#                    waiting for a credit
# The parent may send while S < G. The child knows R = highest S received,
# so G - R credits are outstanding; a jump in S reveals frames that were
# lost or replaced in a sender slot. The window W bounds items in flight
# plus the one waiting in the child's slot.


class _CreditParent:
    """Parent side of credit edges: spend a credit per send; while an edge
    has none, keep only the newest output for it."""

    def _init_parent(self):
        self.granted: dict[str, int] = {}
        self.sent: dict[str, int] = {}
        self.held: dict[str, Item] = {}
        self.exhausted: dict[str, bool] = {}

    def room(self, c: str) -> int:
        return self.granted.get(c, 0) - self.sent.get(c, 0)

    def _send_counted(self, c: str, item: Item, waited: bool = False):
        s = self.sent.get(c, 0) + 1
        self.sent[c] = s
        meta = {**item.meta, "c": s, "h": int(self.exhausted.get(c, False)), "w": int(waited)}
        self.exhausted[c] = self.room(c) == 0
        self.send(c, Item(item.lineage, item.producer, item.payload, meta))

    def _on_credit(self, msg) -> bool:
        c = msg["frm"]
        if msg["g"] <= self.granted.get(c, 0):
            return False  # stale
        self.granted[c] = msg["g"]
        self.flush(c)
        return True

    def flush(self, c: str):
        if c in self.held and self.room(c) > 0:
            self._send_counted(c, self.held.pop(c), waited=True)


class CreditSource(_CreditParent, SourceNode):
    """Each edge gets the newest frame not yet sent on it whenever it has a
    credit."""

    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_parent()
        self.last_sent: dict[str, int] = {}

    def flush(self, c, waited: bool = True):
        f = self.newest
        if f is not None and self.room(c) > 0 and f.producer[1] > self.last_sent.get(c, -1):
            self.last_sent[c] = f.producer[1]
            self._send_counted(c, f, waited)

    def on_frame(self, item):
        for c in self.children:
            self.flush(c, waited=False)

    def on_control(self, msg):
        if msg["t"] == m.CREDIT:
            self._on_credit(msg)


class CreditEngine(_CreditParent, EngineNode):
    """Child side, per parent edge q: a window W_q bounds the inputs from q
    that are in flight or still inside this node (waiting in the slot,
    running, or as an output held for a child without credit). A credit is
    returned to q once an input has left the node: its output was sent to
    some child, or it was rejected, overwritten or replaced. So backpressure
    from the children reaches q, while each window only has to cover its own
    hop. A run is not started while every child already holds an output."""

    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_parent()
        p = self.p
        self.w_max = p.tokens_max
        self.window = {q: max(1, p.credit_init) for q in self.parents}
        self.G = {q: 0 for q in self.parents}  # credits granted to parent q
        self.R = {q: 0 for q in self.parents}  # highest send count seen from q
        self.F = {q: 0 for q in self.parents}  # inputs from q that have left this node
        self.grant_t: dict[str, dict[int, float]] = {q: {} for q in self.parents}
        self.hop_rtt = {q: deque(maxlen=16) for q in self.parents}
        self.last_up = {q: -INF for q in self.parents}
        self.last_down = {q: -INF for q in self.parents}
        self.last_arrival = {q: -INF for q in self.parents}
        self.held_from: dict[str, list[str]] = {}  # child -> parents of its held output
        self.timeout = p.credit_timeout_ms / 1000

    def start(self):
        now = self.now()
        for q in self.parents:
            self.metrics.credit_window[q].append((now, self.window[q]))
            self.last_arrival[q] = now
        self._grant_all()
        self._timeout_tick()

    # -- child side: granting and adapting ----------------------------------

    def _rtt(self, q) -> float:
        return median(self.hop_rtt[q]) if self.hop_rtt[q] else self.r.p50

    def _grant(self, q):
        if self.stopped:
            return
        before = self.G[q]
        now = self.now()
        while self.G[q] - self.F[q] < self.window[q]:
            self.G[q] += 1
            self.grant_t[q][self.G[q]] = now
        if self.G[q] != before:
            self.control(m.CREDIT, q, g=self.G[q])

    def _grant_all(self):
        for q in self.parents:
            self._grant(q)

    def _left(self, parents):
        """Inputs from these parents have left the node: return credits."""
        for q in parents:
            self.F[q] += 1
            self._grant(q)

    def _resize(self, q, delta):
        now = self.now()
        rtt = self._rtt(q)
        if delta > 0:
            if now - self.last_up[q] < rtt or now - self.last_down[q] < rtt:
                return
            self.last_up[q] = now
        else:
            if now - self.last_down[q] < rtt:
                return
            self.last_down[q] = now
        w = max(1, min(self.w_max, self.window[q] + delta))
        if w != self.window[q]:
            self.window[q] = w
            self.metrics.credit_window[q].append((now, w))
            self._grant(q)

    def _timeout_tick(self):
        now = self.now()
        for q in self.parents:
            if self.G[q] > self.R[q] and now - self.last_arrival[q] >= self.timeout:
                # Credits or frames were lost (e.g. an outage): forgive them.
                self.F[q] += self.G[q] - self.R[q]
                self.R[q] = self.G[q]
                self.last_arrival[q] = now
                self._resize(q, -1)
                self._grant(q)
        self.timer(now + self.timeout / 2, self._timeout_tick)

    def on_data(self, frm, item):
        if self.stopped or frm not in self.slots:
            return
        now = self.now()
        s = item.meta.get("c")
        if s is not None:
            if s - self.R[frm] > 1:
                # Frames lost or replaced in a sender slot: they never
                # arrived, so their credits come back now.
                self.F[frm] += s - self.R[frm] - 1
                self._resize(frm, -1)
            t = self.grant_t[frm].pop(s, None)
            if t is not None:
                self.hop_rtt[frm].append(now - t)
            for k in [k for k in self.grant_t[frm] if k < s]:
                del self.grant_t[frm][k]
            self.R[frm] = max(self.R[frm], s)
        idle = not self.running and self.slots[frm] is None
        if idle and item.meta.get("h"):
            # We sat idle while the edge had run out of credits: the window,
            # not the parent, held this input back.
            self._resize(frm, +1)
        self.last_arrival[frm] = now
        self._dropping_from = frm
        super().on_data(frm, item)
        self._grant(frm)

    def on_drop(self, item):
        # An input was overwritten in its slot: too many in flight.
        q = self._dropping_from
        self._resize(q, -1)
        self._left([q])

    def _maybe_run(self):
        if self.children and all(c in self.held for c in self.children):
            return  # nobody could take the output: let inputs wait (newest wins)
        super()._maybe_run()

    def on_run_start(self, taken):
        self._run_from = list(taken)

    def on_reject(self, inputs):
        self._left(self._run_from)

    # -- parent side -----------------------------------------------------------

    def on_output(self, item):
        sent = False
        for c in self.children:
            if self.room(c) > 0:
                self._replace_held(c)
                self._send_counted(c, item)
                sent = True
            else:
                self._replace_held(c)
                self.held[c] = item
                self.held_from[c] = list(self._run_from)
        if sent:
            # The inputs have left (an output went out); copies still held
            # for other children must not return their credits again.
            for c in self.children:
                if self.held.get(c) is item:
                    self.held_from[c] = []
            self._left(self._run_from)

    def _replace_held(self, c):
        """Drop c's held output; its inputs leave if no other child holds it."""
        old = self.held.pop(c, None)
        parents = self.held_from.pop(c, None)
        if old is not None and not any(h is old for h in self.held.values()):
            self._left(parents)

    def _finished(self, payload):
        super()._finished(payload)
        if self.is_leaf and payload is not None:
            self._left(self._run_from)

    def flush(self, c):
        if c in self.held and self.room(c) > 0:
            item = self.held.pop(c)
            parents = self.held_from.pop(c)
            self._send_counted(c, item, waited=True)
            if not any(h is item for h in self.held.values()):
                self._left(parents)
            self._maybe_run()

    def on_control(self, msg):
        if msg["t"] == m.CREDIT:
            self._on_credit(msg)


# ---------------------------------------------------------------------------
# schedule-hop: credit counters with per-hop timing
# ---------------------------------------------------------------------------


class ScheduleHopEngine(CreditEngine):
    """Credit mode, but a credit is held until it's due, so inputs arrive
    when they can be used instead of waiting in the slot.

    For parent edge q, the next input is needed at
        need = max(when this node is free (after whatever is already queued),
                   when its fastest child is next ready - R)
    using R = this node's median run time. "Child ready" is now if a child
    has room, else the child's next credit predicted from the rhythm of its
    recent credits. The credit is sent at need - lead_q. lead_q starts as
    the measured grant-to-arrival time and is corrected on every arrival by
    lead_gain x (arrival - need): late arrivals make it larger, early ones
    smaller. Everything is local to the hop: no phase is passed on, and
    there are no RTT pings, transmit-time estimates or send grids.
    """

    LEAD_MAX = 2.0

    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self.lead: dict[str, float | None] = {q: None for q in self.parents}
        self.due: dict[str, dict[int, float]] = {q: {} for q in self.parents}
        self.grant_timer = {q: None for q in self.parents}
        self.child_gap: dict[str, deque] = {c: deque(maxlen=16) for c in self.children}
        self.child_last: dict[str, float] = {}
        self.timed = {q: False for q in self.parents}  # last grant held back by timing
        self.gain = self.p.lead_gain

    def _child_ready(self) -> float:
        now = self.now()
        if self.is_leaf:
            return now
        best = INF
        for c in self.children:
            if self.room(c) - (1 if c in self.held else 0) > 0:
                return now
            last, gaps = self.child_last.get(c), self.child_gap[c]
            if last is None or not gaps:
                return now  # no rhythm yet: don't hold back
            best = min(best, max(now, last + median(gaps)))
        return best

    def _need(self, q) -> float:
        now = self.now()
        r = self.r.p50
        free = max(now, self.start_t + r) if self.running else now
        queued = (self.G[q] - self.R[q]) + (1 if self.slots[q] is not None else 0)
        return max(free + queued * r, self._child_ready() - r)

    def _grant(self, q):
        if self.stopped:
            return
        now = self.now()
        before = self.G[q]
        self.cancel(self.grant_timer[q])
        self.grant_timer[q] = None
        while self.G[q] - self.F[q] < self.window[q]:
            need = self._need(q)
            lead = self.lead[q] if self.lead[q] is not None else 0.0
            at = need - lead
            if at > now + 1e-4:
                self.grant_timer[q] = self.timer(at, lambda q=q: self._grant(q))
                self.timed[q] = True
                break
            self.timed[q] = False
            self.G[q] += 1
            self.grant_t[q][self.G[q]] = now
            self.due[q][self.G[q]] = need
        if self.G[q] != before:
            self.control(m.CREDIT, q, g=self.G[q])

    def on_data(self, frm, item):
        if not self.stopped and frm in self.slots:
            s = item.meta.get("c")
            now = self.now()
            due = self.due[frm].pop(s, None) if s is not None else None
            granted = self.grant_t[frm].get(s) if s is not None else None
            for k in [k for k in self.due[frm] if s is not None and k < s]:
                del self.due[frm][k]
            if self.lead[frm] is None and granted is not None:
                self.lead[frm] = now - granted
            elif due is not None:
                err = now - due
                # Late only counts if the input sat ready at the parent
                # waiting for this credit (w = 1); otherwise the parent itself
                # was the bottleneck and asking earlier can't help.
                if err < 0 or item.meta.get("w"):
                    lead = self.lead[frm] + self.gain * err
                    self.lead[frm] = min(self.LEAD_MAX, max(0.0, lead))
        super().on_data(frm, item)

    def _resize(self, q, delta):
        if delta > 0 and self.timed[q]:
            return  # timing, not the window, is holding grants back
        super()._resize(q, delta)

    def on_control(self, msg):
        if msg["t"] == m.CREDIT:
            c = msg["frm"]
            now = self.now()
            new = msg["g"] - self.granted.get(c, 0)
            if new > 0 and c in self.child_gap:
                if c in self.child_last:
                    self.child_gap[c].append((now - self.child_last[c]) / new)
                self.child_last[c] = now
            if self._on_credit(msg):
                self._grant_all()  # the child's rhythm may move our need earlier
        else:
            super().on_control(msg)


# ---------------------------------------------------------------------------
# segment: fixed token counts per fan-out group
# ---------------------------------------------------------------------------
#
# A head (a Source, or an engine with several children) splits its children
# into a local group (edges on one host) and a remote group (edges crossing
# the network). Each group has a fixed count N: segment_remote_tokens if the
# group's segment crosses the network anywhere before the next head or
# leaf, else segment_local_tokens. A group admits an output while fewer than
# N of its frames are out, stamps it [head, group, id] and sends ONE copy to
# all its children ("a+b": across the link it crosses once and the far host
# delivers it to each); otherwise it keeps the newest one waiting.
#
# Engines inside a segment just push. Every path below a group ends in one
# event, reported to the head as {"t": "seg", "ev": [[group, id, what]]}:
#   used  a leaf result, a filter rejection, or the next head admitting it
#   drop  overwritten in a slot, replaced in a sender slot, or replaced while
#         waiting at the next head (this path never used it)
#   lost  the group's single copy was replaced before leaving (every path)
# The token returns on the first "used". A frame every path dropped (or
# lost) was sent for nothing: its token is parked until token_timeout_ms
# instead of being reused at once, as long as another frame of the group is
# still out (that frame will come back and keep the group going, so parking
# can't stall it). At a join that waits for every branch, the frame that
# overwrote the old one is waiting there, so the fast branch's excess
# tokens park instead of overrunning the join.
# An oversized count so takes its own excess out of circulation and probes
# again every timeout, while the count still follows the fastest consumer
# and slower ones skip frames for free. Tokens missing for token_timeout_ms
# are reclaimed.


@dataclass
class _Group:
    kind: str
    children: list[str]
    n: int
    out: dict = None  # id -> [paths still pending (0 = parked), admit or park time]
    held: Item | None = None
    held_stamps: list = None
    held_reported: bool = False
    next_id: int = 0

    def __post_init__(self):
        self.out = {}
        self.held_stamps = []


def _stamps(items) -> list:
    seen = []
    for it in items:
        for s in it.meta.get("seg", []):
            if s not in seen:
                seen.append(s)
    return seen


def _report(node: Node, stamps, what: str):
    by_head = defaultdict(list)
    for head, group, fid in stamps:
        by_head[head].append([group, fid, what])
    for head, evs in by_head.items():
        node.control(m.SEG, head, ev=evs)


def segment_returns_for_replaced(spec: FlowSpec, parent: str, raw: bytes) -> list[dict]:
    """A frame was replaced in a sender slot before it left: tell the heads.
    The sender's own group copy is lost on every path; a frame merely passing
    through (stamped by an upstream head) loses one path."""
    by_head = defaultdict(list)
    for head, group, fid in Item.decode(raw).meta.get("seg", []):
        by_head[head].append([group, fid, "lost" if head == parent else "drop"])
    return [{"t": m.SEG, "f": spec.name, "frm": parent, "to": h, "ev": evs} for h, evs in by_head.items()]


def returns_for_replaced(spec: FlowSpec, parent: str, raw: bytes) -> list[dict]:
    if spec.mode == SEGMENT:
        return segment_returns_for_replaced(spec, parent, raw)
    return tokens_for_replaced(spec, parent, raw)


class _SegmentHead:
    def _init_groups(self):
        p = self.p
        self.seg_groups: dict[str, _Group] = {}
        for kind, children in self.spec.groups(self.name).items():
            crosses = self.spec.segment_crosses(self.name, kind)
            n = p.segment_remote_tokens if crosses else (p.segment_local_tokens or 2)
            self.seg_groups[kind] = _Group(kind, children, max(1, n))
        self.seg_timeout = p.token_timeout_ms / 1000

    def _start_groups(self):
        if self.seg_groups:
            self._reclaim_tick()

    def _dispatch(self, g: _Group, item: Item):
        g.next_id += 1
        g.out[g.next_id] = [len(g.children), self.now()]
        meta = {k: v for k, v in item.meta.items() if k != "seg"}
        meta["seg"] = [[self.name, g.kind, g.next_id]]
        meta["mc"] = len(g.children)
        self.send("+".join(g.children), Item(item.lineage, item.producer, item.payload, meta))

    def _admit(self, item: Item, in_stamps: list):
        """Offer an output to every group; report its input paths used once
        some group takes it."""
        used = False
        for g in self.seg_groups.values():
            if len(g.out) < g.n:
                self._drop_held(g)
                self._dispatch(g, item)
                used = True
            else:
                self._drop_held(g)
                g.held, g.held_stamps, g.held_reported = item, in_stamps, False
        if used:
            _report(self, in_stamps, "used")
            for g in self.seg_groups.values():
                if g.held is item:
                    g.held_reported = True

    def _drop_held(self, g: _Group):
        old = g.held
        if old is None:
            return
        g.held = None
        if not g.held_reported and not any(o.held is old for o in self.seg_groups.values()):
            _report(self, g.held_stamps, "drop")  # never used by any group

    def _release(self, g: _Group):
        if g.held is not None and len(g.out) < g.n:
            item, stamps, reported = g.held, g.held_stamps, g.held_reported
            g.held = None
            self._dispatch(g, item)
            if not reported:
                _report(self, stamps, "used")
                for o in self.seg_groups.values():
                    if o.held is item:
                        o.held_reported = True

    def _unused(self, g: _Group):
        now = self.now()
        self.metrics.unused[g.kind].append(now)
        if g.kind == "remote":
            self.metrics.wasted_transfers.append(now)  # crossed the link, nobody ran it

    def _on_seg(self, msg):
        touched = set()
        for kind, fid, what in msg["ev"]:
            g = self.seg_groups.get(kind)
            rec = g.out.get(fid) if g is not None else None
            if rec is None:
                continue  # already returned
            if rec[0] == 0:
                continue  # parked: comes back at the timeout
            if what == "used":
                del g.out[fid]
            else:
                rec[0] = 0 if what == "lost" else rec[0] - 1
                if rec[0] > 0:
                    continue
                self._unused(g)
                if any(r[0] > 0 for f, r in g.out.items() if f != fid):
                    rec[1] = self.now()  # park this token until the timeout
                    self.metrics.token_returns["parked"].append(rec[1])
                    continue
                del g.out[fid]
            touched.add(kind)
        for kind in touched:
            self._release(self.seg_groups[kind])

    def _reclaim_tick(self):
        now = self.now()
        for g in self.seg_groups.values():
            for fid, (_, t) in list(g.out.items()):
                if now - t >= self.seg_timeout:
                    del g.out[fid]
                    self.metrics.tokens_reclaimed.append(now)
            self._release(g)
        self.timer(now + self.seg_timeout / 4, self._reclaim_tick)


class SegmentSource(_SegmentHead, SourceNode):
    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_groups()

    def start(self):
        super().start()
        self._start_groups()

    def on_frame(self, item):
        self._admit(item, [])

    def on_control(self, msg):
        if msg["t"] == m.SEG:
            self._on_seg(msg)


class SegmentEngine(_SegmentHead, EngineNode):
    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_groups()
        self._in_stamps = []

    def start(self):
        self._start_groups()

    def on_run_start(self, taken):
        self._in_stamps = _stamps(taken.values())

    def on_output(self, item):
        if self.seg_groups:
            self._admit(item, self._in_stamps)
        else:  # single child: just push, the frame stays in its segment
            item.meta["seg"] = self._in_stamps
            for c in self.children:
                self.send(c, item)

    def _finished(self, payload):
        stamps = self._in_stamps
        super()._finished(payload)
        if self.is_leaf and payload is not None:
            _report(self, stamps, "used")

    def on_reject(self, inputs):
        _report(self, self._in_stamps, "used")  # a filter decision is a use

    def on_drop(self, item):
        _report(self, item.meta.get("seg", []), "drop")

    def on_control(self, msg):
        if msg["t"] == m.SEG:
            self._on_seg(msg)


# ---------------------------------------------------------------------------
# segment (default): local edges pulled, remote edges fixed tokens
# ---------------------------------------------------------------------------
#
# Local edges (same host): a child asks its parent for input ("want") only
# when it is idle (not running, slot empty) and could pass a result on. The
# parent answers at once with the newest thing it has: a Source its newest
# frame, an engine the output it holds for that child. An engine keeps at
# most one output ready per local child: it only runs while some child could
# take the result (one is asking, or holds nothing from it yet). So local
# consumers of a Source never queue an input, and between engines one ready
# output keeps the pipeline from bubbling.
#
# Remote edges (crossing the network): each is a branch with its own fixed
# count of segment_remote_tokens. When an input arrives at a node with remote
# children it goes out once, right away, to every remote child with a free
# token (one copy per host, addressed "a+b"); arrival drives sending.
# A remote branch's token comes back when its chain on the far side ends:
#   used  leaf result, filter rejection, or a fan-out (any node with several
#         children, or with remote children) forwarding it to some child
#   drop  skipped (overwritten in a slot, or a held output replaced): parked
#         until token_timeout_ms while another frame of the branch is out,
#         else returned; parked tokens come back one per branch per tick
#   lost  the copy was replaced in the sender slot before leaving (as drop)
# A node with remote children watches the link: while it is down, those
# tokens are held (no timeouts, no sends); when it returns they're released.


class _Branch:
    def __init__(self, n: int, host: str):
        self.n = n
        self.host = host
        self.out: dict[int, list] = {}  # id -> [state: out | parked, time]


class _SegNode:
    def _init_seg(self):
        p = self.p
        spec, name = self.spec, self.name
        self.remote_children = [c for c in self.children if spec.is_network_edge(name, c)]
        self.local_children = [c for c in self.children if c not in self.remote_children]
        self.branches = {c: _Branch(max(1, p.segment_remote_tokens), spec.host(c))
                         for c in self.remote_children}
        # A fan-out ends the upstream remote branch's chain when it forwards.
        self.fanout = len(self.children) > 1 or bool(self.remote_children)
        self.want = {c: False for c in self.local_children}
        self.held: dict[str, list] = {}  # local child -> [item, upstream stamps, reported]
        self.next_id = 0
        self.copies: dict[int, list] = {}
        self.link_up = True
        self.seg_timeout = p.token_timeout_ms / 1000

    def _start_seg(self):
        if self.branches:
            self._reclaim_tick()

    # -- remote branches -----------------------------------------------------

    def _free(self, c: str) -> bool:
        return self.link_up and len(self.branches[c].out) < self.branches[c].n

    def _send_remote(self, item: Item) -> bool:
        """Send an arrival once to every remote child with a free token."""
        ready = [c for c in self.remote_children if self._free(c)]
        if not ready:
            return False
        now = self.now()
        self.next_id += 1
        fid = self.next_id
        by_host = defaultdict(list)
        for c in ready:
            by_host[self.branches[c].host].append(c)
            self.branches[c].out[fid] = ["out", now]
        self.copies[fid] = [len(ready), False]
        for cs in by_host.values():
            meta = {k: v for k, v in item.meta.items() if k != "seg"}
            meta["seg"] = [[self.name, c, fid] for c in cs]
            meta["mc"] = len(cs)
            self.send("+".join(cs), Item(item.lineage, item.producer, item.payload, meta))
        return True

    def _copy_done(self, fid: int, used: bool):
        rec = self.copies.get(fid)
        if rec is None:
            return
        rec[0] -= 1
        rec[1] = rec[1] or used
        if rec[0] <= 0:
            del self.copies[fid]
            if not rec[1]:
                self.metrics.wasted_transfers.append(self.now())  # crossed, nobody ran it

    def _on_seg(self, msg):
        now = self.now()
        for child, fid, what in msg["ev"]:
            b = self.branches.get(child)
            rec = b.out.get(fid) if b is not None else None
            if rec is None or rec[0] == "parked":
                continue
            if what == "used":
                del b.out[fid]
                self._copy_done(fid, True)
                continue
            self.metrics.unused[child].append(now)  # drop / lost
            self._copy_done(fid, False)
            if any(r[0] == "out" for f, r in b.out.items() if f != fid):
                rec[0], rec[1] = "parked", now
                self.metrics.token_returns["parked"].append(now)
            else:
                del b.out[fid]
        self._capacity_changed()

    def _reclaim_tick(self):
        now = self.now()
        if self.link_up:  # hold remote tokens through an outage
            for b in self.branches.values():
                unparked = False
                for fid, (state, t) in sorted(b.out.items(), key=lambda kv: kv[1][1]):
                    if now - t < self.seg_timeout:
                        continue
                    if state == "parked":
                        if unparked:
                            continue  # one parked token back per branch per tick
                        unparked = True
                    else:
                        self.metrics.tokens_reclaimed.append(now)
                    del b.out[fid]
        self._capacity_changed()
        self.timer(now + self.seg_timeout / 4, self._reclaim_tick)

    def on_link(self, up: bool):
        if not self.branches:
            return
        was, self.link_up = self.link_up, up
        if up and not was:
            for b in self.branches.values():
                b.out.clear()  # frames out across the link were lost
            self._capacity_changed()

    def _capacity_changed(self):
        pass

    def on_control(self, msg):
        if msg["t"] == m.SEG:
            self._on_seg(msg)
        elif msg["t"] == m.WANT:
            self._on_want(msg["frm"])


class BranchSource(_SegNode, SourceNode):
    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_seg()
        self.last_sent: dict[str, int] = {}

    def start(self):
        super().start()
        self._start_seg()

    def _send_local(self, c: str, item: Item):
        self.want[c] = False
        self.last_sent[c] = item.producer[1]
        self.send(c, item)

    def _on_want(self, c: str):
        f = self.newest
        if f is not None and f.producer[1] > self.last_sent.get(c, -1):
            self._send_local(c, f)  # the newest frame, right now
        else:
            self.want[c] = True  # send the next frame when it arrives

    def on_frame(self, item):
        for c in self.local_children:
            if self.want[c]:
                self._send_local(c, item)
        self._send_remote(item)


class BranchEngine(_SegNode, EngineNode):
    def __init__(self, spec, name, io):
        super().__init__(spec, name, io)
        self._init_seg()
        self._in_stamps = []
        self.local_parents = [q for q in self.parents if not spec.is_network_edge(q, name)]
        self.requested = {q: False for q in self.local_parents}

    def start(self):
        self._start_seg()
        self._request()

    # -- as a child: ask local parents for input when idle ---------------------

    def _deliverable(self) -> bool:
        """Could a result be passed on right now (or held for one child)?"""
        if self.is_leaf:
            return True
        if any(self._free(c) for c in self.remote_children):
            return True
        return any(c not in self.held for c in self.local_children)

    def _request(self):
        if self.stopped or self.running or not self._deliverable():
            return
        for q in self.local_parents:
            if self.slots[q] is None and not self.requested[q]:
                self.requested[q] = True
                self.control(m.WANT, q)

    def on_data(self, frm, item):
        if frm in self.requested:
            self.requested[frm] = False
        # A copy addressed to several children carries every branch's stamp;
        # keep only this branch's (and any from other heads).
        stamps = item.meta.get("seg")
        if stamps:
            mine = [x for x in stamps if not (x[0] == frm and x[1] != self.name)]
            if len(mine) != len(stamps):
                item = Item(item.lineage, item.producer, item.payload, {**item.meta, "seg": mine})
        super().on_data(frm, item)

    def _maybe_run(self):
        if not self._deliverable():
            return  # nothing could take the output: let the input wait
        super()._maybe_run()

    def after_run(self):
        self._request()

    def _capacity_changed(self):
        self._maybe_run()
        self._request()

    # -- as a parent ---------------------------------------------------------

    def _on_want(self, c: str):
        h = self.held.pop(c, None)
        if h is not None:
            item, stamps, reported = h
            self.send(c, item)
            if stamps and not reported:
                _report(self, stamps, "used")
                for other in self.held.values():
                    if other[0] is item:
                        other[2] = True
        else:
            self.want[c] = True
        self._capacity_changed()

    def on_run_start(self, taken):
        self._in_stamps = _stamps(taken.values())

    def on_output(self, item):
        stamps = self._in_stamps
        if not self.fanout:
            # Single local child inside a chain: pass the stamps along.
            item.meta["seg"] = stamps
            c = self.local_children[0]
            if self.want[c]:
                self.want[c] = False
                self.send(c, item)
            else:
                self._hold(c, item, stamps, False)
            return
        sent = self._send_remote(item)
        clean = Item(item.lineage, item.producer, item.payload,
                     {k: v for k, v in item.meta.items() if k != "seg"})
        wanting = [c for c in self.local_children if self.want[c]]
        for c in wanting:
            self.want[c] = False
        if wanting:
            self.send("+".join(wanting), clean)
            sent = True
        for c in self.local_children:
            if c not in wanting:
                self._hold(c, clean, stamps, sent)
        if stamps:
            if sent:
                _report(self, stamps, "used")  # a fan-out forwarding it ends the chain
            elif not any(h[0] is clean for h in self.held.values()):
                _report(self, stamps, "drop")  # nowhere to go (e.g. the link dropped)

    def _hold(self, c: str, item: Item, stamps: list, reported: bool):
        old = self.held.get(c)
        self.held[c] = [item, stamps, reported]
        if old is not None and old[1] and not old[2] and not any(h[0] is old[0] for h in self.held.values()):
            _report(self, old[1], "drop")  # replaced before anyone took it

    def _finished(self, payload):
        stamps = self._in_stamps
        super()._finished(payload)
        if self.is_leaf and payload is not None and stamps:
            _report(self, stamps, "used")

    def on_reject(self, inputs):
        if self._in_stamps:
            _report(self, self._in_stamps, "used")

    def on_drop(self, item):
        if item.meta.get("seg"):
            _report(self, item.meta["seg"], "drop")


# ---------------------------------------------------------------------------

_CLASSES = {
    SCHEDULE: (ScheduleSource, ScheduleEngine),
    PUSH: (PushSource, PushEngine),
    PULL: (PullSource, PullEngine),
    TOKEN: (TokenSource, TokenEngine),
    TOKEN_ADAPTIVE: (AdaptiveTokenSource, TokenEngine),
    CREDIT: (CreditSource, CreditEngine),
    SCHEDULE_HOP: (CreditSource, ScheduleHopEngine),
    SEGMENT: (SegmentSource, SegmentEngine),
}


def make_node(spec: FlowSpec, name: str, io: NodeIO) -> Node:
    source_cls, engine_cls = _CLASSES[spec.mode]
    if spec.mode == SEGMENT and spec.params.segment_branch_tokens:
        source_cls, engine_cls = BranchSource, BranchEngine
    cls = source_cls if spec.node(name).kind == SOURCE else engine_cls
    return cls(spec, name, io)
