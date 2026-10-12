"""Network edge agent: one per side of the client <-> remote link.

The client's FlowRouter is the agent on the local side; the remote server is
the agent on the remote side. Each applies the protocol's network-edge
corrections exactly once, to schedule-mode demands crossing the link:

- receiving side of data (where the child lives), before the demand goes up
  the link: Worst = max(Worst, T); Phase -= T
- sending side of data (where the parent lives), on receiving the demand
  from the link: Phase -= RTT

RTT comes from pings this side sends over the control channel (median of
recent samples); T from per-frame first-to-last-byte arrival times measured
on this side for frames on that edge's channel.
"""

from .estimators import Rtt, Transmit
from .flow import REMOTE, FlowSpec, channel
from .messages import DEMAND


class EdgeAgent:
    def __init__(self, side_is_remote: bool):
        self.side_is_remote = side_is_remote
        self.rtt = Rtt()
        self.transmit: dict[str, Transmit] = {}

    def on_frame(self, chan: str, size: int, elapsed: float):
        est = self.transmit.get(chan)
        if est is None:
            est = self.transmit[chan] = Transmit()
        est.update(size, elapsed)

    def t_for(self, chan: str, q: float = 90) -> float:
        """T for an edge's channel at percentile q of time per byte."""
        est = self.transmit.get(chan)
        return 0.0 if est is None else est.at(q)

    def _crosses(self, spec: FlowSpec, msg: dict) -> bool:
        return spec.is_network_edge(msg["to"], msg["frm"])

    def outgoing(self, spec: FlowSpec, msg: dict) -> dict:
        """A control message is about to cross the link from this side."""
        if msg["t"] != DEMAND or not self._crosses(spec, msg):
            return msg
        child_here = (spec.host(msg["frm"]) == REMOTE) == self.side_is_remote
        if child_here:
            t = self.t_for(channel(spec.name, msg["to"], msg["frm"]), spec.params.transmit_pct)
            msg = dict(msg, worst=max(msg["worst"], t), phase=msg["phase"] - t)
        return msg

    def incoming(self, spec: FlowSpec, msg: dict) -> dict:
        """A control message arrived over the link on this side."""
        if msg["t"] != DEMAND or not self._crosses(spec, msg):
            return msg
        parent_here = (spec.host(msg["to"]) == REMOTE) == self.side_is_remote
        if parent_here:
            msg = dict(msg, phase=msg["phase"] - self.rtt.value)
        return msg
