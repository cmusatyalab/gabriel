"""Virtual-clock harness: runs flows' node state machines in simulated time.

The same `Node` classes run here as in the servers and client; only the
`NodeIO` differs. The network between the client and the remote host is a
simple model (one-way latency, rate, outages), and the edge agents apply the
same demand corrections as the real router. Used by the tests, and by the
simulator's ``virtual`` backend for fast parameter sweeps without processes
or root.
"""

import heapq
import itertools
import random
from dataclasses import dataclass, field

from .edge import EdgeAgent
from .engine import Behavior
from .flow import CLIENT, REMOTE, SINK, FlowSpec, channel, targets
from .messages import Item
from .metrics import Sink, node_report, summarize
from .nodes import make_node, returns_for_replaced


class _Handle:
    __slots__ = ("cancelled",)

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class Clock:
    def __init__(self, start: float = 1.0):
        self.t = start
        self._heap = []
        self._ids = itertools.count()

    def now(self) -> float:
        return self.t

    def call_at(self, when: float, callback) -> _Handle:
        h = _Handle()
        heapq.heappush(self._heap, (max(when, self.t), next(self._ids), h, callback))
        return h

    def run_until(self, t_end: float):
        while self._heap and self._heap[0][0] <= t_end:
            when, _, h, cb = heapq.heappop(self._heap)
            self.t = when
            if not h.cancelled:
                cb()
        self.t = t_end


@dataclass
class VirtualLink:
    """Client <-> remote link model. Per direction, frames are serialized at
    `rate_mbit` (sender-side single slot per channel, like the transport)
    and then delayed by `delay_ms` (+ uniform jitter). During an outage
    everything sent is lost. `events` are (time offset, changes) pairs."""

    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    rate_mbit: float | None = None
    loss_pct: float = 0.0
    events: list = field(default_factory=list)
    down: bool = False


class _Direction:
    """One direction of the virtual link: serialization queue of one slot
    per channel, then propagation delay."""

    def __init__(self, world: "VirtualWorld"):
        self.world = world
        self.busy_until = 0.0
        self.outbox: dict[str, tuple[int, object]] = {}
        self.in_flight = False

    def send(self, chan: str, size: int, deliver, on_timing=None, on_replace=None):
        link = self.world.link
        if link.rate_mbit is None:
            self._propagate(deliver)
            if on_timing:
                on_timing(size, 0.0)
            return
        replaced = self.outbox.get(chan)
        self.outbox[chan] = (size, deliver, on_timing, on_replace)  # newest wins
        if replaced is not None and replaced[3] is not None:
            replaced[3]()
        self._pump()

    def _pump(self):
        if self.in_flight or not self.outbox:
            return
        chan = next(iter(self.outbox))
        size, deliver, on_timing, _ = self.outbox.pop(chan)
        tx = size * 8 / (self.world.link.rate_mbit * 1e6)
        self.in_flight = True
        clock = self.world.clock

        def done():
            self.in_flight = False
            self._propagate(deliver)
            if on_timing:
                on_timing(size, tx)
            self._pump()

        clock.call_at(clock.now() + tx, done)

    def _propagate(self, deliver):
        link = self.world.link
        if link.down or self.world.rng.random() * 100 < link.loss_pct:
            return
        d = link.delay_ms + self.world.rng.uniform(0, link.jitter_ms)
        self.world.clock.call_at(self.world.clock.now() + d / 1000, deliver)


class _IO:
    def __init__(self, world: "VirtualWorld", spec: FlowSpec, name: str):
        self.world = world
        self.spec = spec
        self.name = name

    def now(self):
        return self.world.clock.now()

    def call_at(self, when, callback):
        return self.world.clock.call_at(when, callback)

    def send_data(self, frm, to, item: Item):
        self.world.route_data(self.spec, frm, to, item)

    def send_control(self, msg):
        self.world.route_control(self.spec, msg)

    def run_engine(self, node, inputs, done):
        behavior = self.world.behaviors[(self.spec.name, node)]
        seconds, passes = behavior.plan()
        output = behavior.output if passes else None
        self.world.clock.call_at(self.now() + seconds, lambda: done(output))


class VirtualWorld:
    LOCAL_HOP = 50e-6  # shared-memory hop

    def __init__(self, specs: list[FlowSpec], link: VirtualLink | None = None, seed: int = 0):
        self.clock = Clock()
        self.link = link or VirtualLink()
        self.rng = random.Random(seed)
        self.up = _Direction(self)  # client -> remote
        self.down = _Direction(self)  # remote -> client
        self.agent_client = EdgeAgent(side_is_remote=False)
        self.agent_remote = EdgeAgent(side_is_remote=True)
        self.specs = {s.name: s for s in specs}
        self.nodes = {}
        self.behaviors = {}
        self.sinks = {}
        for spec in specs:
            spec.validate()
            self.sinks[spec.name] = Sink(spec, self.clock.now, lambda msg, s=spec: self.route_control(s, msg))
            for n in spec.nodes:
                self.behaviors[(spec.name, n.name)] = Behavior(n, f"{spec.seed}/{spec.name}/{n.name}")
                self.nodes[(spec.name, n.name)] = make_node(spec, n.name, _IO(self, spec, n.name))
        self._schedule_link_events()
        self._ping()

    def _schedule_link_events(self):
        t0 = self.clock.now()
        for offset, changes in self.link.events:
            def apply(changes=changes):
                was = self.link.down
                for k, v in changes.items():
                    setattr(self.link, k, v)
                if self.link.down != was:
                    for node in self.nodes.values():
                        node.on_link(not self.link.down)
            self.clock.call_at(t0 + offset, apply)

    def _ping(self):
        # Both sides measure RTT; in the model it is the two one-way delays.
        if not self.link.down:
            for agent in (self.agent_client, self.agent_remote):
                one_way = [self.link.delay_ms + self.rng.uniform(0, self.link.jitter_ms) for _ in range(2)]
                agent.rtt.update(sum(one_way) / 1000)
        self.clock.call_at(self.clock.now() + 0.1, self._ping)

    # -- routing -------------------------------------------------------------

    def _hop(self, spec, frm_host, to_host, chan, size, deliver, timing_agent=None, on_replace=None):
        crosses = (frm_host == REMOTE) != (to_host == REMOTE)
        if not crosses:
            self.clock.call_at(self.clock.now() + self.LOCAL_HOP, deliver)
            return
        direction = self.down if frm_host == REMOTE else self.up
        on_timing = None
        if timing_agent is not None:
            def on_timing(sz, tx):
                timing_agent.on_frame(chan, sz, tx)
        direction.send(chan, size, deliver, on_timing, on_replace)

    def route_data(self, spec, frm, to, item: Item):
        frm_host, to_host = spec.host(frm), spec.host(to)
        chan = channel(spec.name, frm, to)
        size = len(item.payload) + 64
        agent = self.agent_remote if to_host == REMOTE else self.agent_client

        def deliver():
            if to == SINK:
                self.sinks[spec.name].on_result(frm, item)
            else:
                for t in targets(to):  # one transfer, delivered to each target
                    self.nodes[(spec.name, t)].on_data(frm, item)

        def replaced():
            for msg in returns_for_replaced(spec, frm, item.encode()):
                self.route_control(spec, msg)

        self._hop(spec, frm_host, to_host, chan, size, deliver, agent, replaced)

    def route_control(self, spec, msg):
        frm_host, to_host = spec.host(msg["frm"]), spec.host(msg["to"])
        crosses = (frm_host == REMOTE) != (to_host == REMOTE)
        if crosses:
            out_agent = self.agent_remote if frm_host == REMOTE else self.agent_client
            in_agent = self.agent_client if frm_host == REMOTE else self.agent_remote
            msg = out_agent.outgoing(spec, msg)

        def deliver(msg=msg):
            if crosses:
                msg = in_agent.incoming(spec, msg)
            self.nodes[(spec.name, msg["to"])].on_control(msg)

        if not crosses:
            self.clock.call_at(self.clock.now() + self.LOCAL_HOP, deliver)
        else:
            # Control is small and reliable: latency only (lost while down).
            direction = self.down if frm_host == REMOTE else self.up
            direction._propagate(deliver)

    # -- running -------------------------------------------------------------

    def run(self, duration: float, warmup: float = 1.0) -> dict[str, dict]:
        """Start every node, run, and summarize each flow over
        [start + warmup, start + duration]."""
        t_start = self.clock.now()
        for node in self.nodes.values():
            node.start()
        self.clock.run_until(t_start + duration)
        t0, t1 = t_start + warmup, t_start + duration
        for node in self.nodes.values():
            node.stop()
        out = {}
        for name, spec in self.specs.items():
            reports = {
                n.name: node_report(self.nodes[(name, n.name)], t0, t1) for n in spec.nodes
            }
            out[name] = summarize(spec, self.sinks[name], reports, t0, t1)
        return out


__all__ = ["VirtualWorld", "VirtualLink", "Clock", "CLIENT"]
