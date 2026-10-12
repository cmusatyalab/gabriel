"""Flow specifications: the compute graph, where each node runs, and the
pacing mode. A spec is plain data so the client can send the same spec to
every server; each server instantiates the nodes placed on its host.

Hosts:
    client  Sources (and the result sink). Shares a host with local engines.
    local   engines managed by the local server (shared memory to the client)
    remote  engines managed by the remote server (QUIC through the client)
"""

from dataclasses import asdict, dataclass, field, fields

import networkx as nx

CLIENT = "client"
LOCAL = "local"
REMOTE = "remote"
HOSTS = (CLIENT, LOCAL, REMOTE)

SOURCE = "source"
ENGINE = "engine"

# Pacing modes: the v1 protocol and the baselines it is measured against.
SCHEDULE = "schedule"  # v1: demand schedules (interval, phase, lease)
PUSH = "push"  # publish every sample, forward every output
PULL = "pull"  # one-ahead pull: each engine requests from the previous one
TOKEN = "token"  # CONWIP: N tokens per Source, returned on result or drop
TOKEN_ADAPTIVE = "token-adaptive"  # token, with N adjusted per round trip
TOKEN_MODES = (TOKEN, TOKEN_ADAPTIVE)
CREDIT = "credit"  # hop-by-hop credits per edge, adapted by each child
SCHEDULE_HOP = "schedule-hop"  # credit, with each credit timed to when it's needed
SEGMENT = "segment"  # fixed token counts per fan-out group (local / remote)
MODES = (SCHEDULE, PUSH, PULL, TOKEN, TOKEN_ADAPTIVE, CREDIT, SCHEDULE_HOP, SEGMENT)

# Name of the result consumer at the end of every leaf's result edge.
SINK = "@sink"
_RESERVED = "|@+"


@dataclass
class Service:
    """Distribution of an engine's run time (or a Source's frame jitter).

    dist: const | uniform | normal | exp | lognormal
    mean_ms: mean in milliseconds
    cv: coefficient of variation (std / mean) for normal and lognormal;
        half-width / mean for uniform
    min_ms: lower bound applied after sampling
    """

    dist: str = "const"
    mean_ms: float = 10.0
    cv: float = 0.0
    min_ms: float = 0.0


@dataclass
class NodeSpec:
    """One node of the graph.

    Sources use period_ms, frame_bytes and jitter_ms. Engines use host,
    service, output_bytes, pass_rate and grace_ms. `r_seed_ms` seeds the run
    time estimate (the scheduler does not know engine speeds in advance).
    """

    name: str
    kind: str = ENGINE
    host: str = LOCAL
    parents: list[str] = field(default_factory=list)
    # Source
    period_ms: float = 33.3
    frame_bytes: int = 10_000
    jitter_ms: float = 0.0
    # Engine
    service: Service = field(default_factory=Service)
    output_bytes: int = 1_000
    pass_rate: float = 1.0
    grace_ms: float | None = None  # joins: None = wait for every parent
    r_seed_ms: float | None = None

    def __post_init__(self):
        if isinstance(self.service, dict):
            self.service = Service(**self.service)
        if isinstance(self.grace_ms, str):
            if self.grace_ms.lower() != "none":
                raise ValueError(f"node {self.name}: grace_ms must be a number or \"none\"")
            self.grace_ms = None  # TOML has no null: "none" = wait for every parent
        if self.kind == SOURCE:
            self.host = CLIENT


@dataclass
class Params:
    """Protocol constants (see protocol.txt)."""

    c: float = 3.0  # lease = C x max(R, W)
    w_ms: float = 50.0  # idle heartbeat period; lease is never below C x W
    eps_ms: float = 1.0  # send-time tolerance at interior nodes
    r_window: int = 32  # run times kept for percentiles
    r_seed_ms: float = 10.0  # default run-time seed
    pace_pct: float = 50.0  # schedule: run-time percentile for Best/Worst (protocol.txt: 80)
    transmit_pct: float = 50.0  # schedule: time-per-byte percentile for T (protocol.txt: 90)
    tokens: int = 3  # token mode: tokens per Source (token-adaptive: starting count)
    tokens_max: int = 64  # token-adaptive / credit: upper bound on a window
    credit_init: int = 2  # credit: starting window per edge
    credit_timeout_ms: float = 1000.0  # credit: re-grant if nothing arrives this long
    lead_gain: float = 0.5  # schedule-hop: fraction of each arrival error fed back into the lead
    segment_local_tokens: int = 2  # segment, group mode only: tokens for a local group
    segment_remote_tokens: int = 6  # segment: fixed token count per remote branch (edge crossing the network)
    segment_branch_tokens: bool = True  # segment: remote tokens per branch, local edges pulled (False = per-group tokens)
    token_timeout_ms: float = 2000.0  # token mode: reclaim a lost token
    pull_timeout_ms: float = 500.0  # pull mode: re-request after this long
    flap_window_ms: float = 1000.0  # lease expiry then a demand within this = flap
    ping_interval_ms: float = 100.0  # network edges: RTT ping period
    engine_process: bool = True  # run engines as separate processes


@dataclass
class FlowSpec:
    name: str
    nodes: list[NodeSpec]
    mode: str = SCHEDULE
    params: Params = field(default_factory=Params)
    seed: int = 0

    def __post_init__(self):
        self.nodes = [n if isinstance(n, NodeSpec) else NodeSpec(**n) for n in self.nodes]
        if isinstance(self.params, dict):
            self.params = Params(**self.params)
        self._graph = None

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "seed": self.seed,
            "params": asdict(self.params),
            "nodes": [asdict(n) for n in self.nodes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FlowSpec":
        known = {f.name for f in fields(Params)}
        params = {k: v for k, v in data.get("params", {}).items() if k in known}
        return cls(
            name=data["name"],
            mode=data.get("mode", SCHEDULE),
            seed=data.get("seed", 0),
            params=Params(**params),
            nodes=[NodeSpec(**n) for n in data["nodes"]],
        )

    def with_mode(self, mode: str, name: str | None = None) -> "FlowSpec":
        spec = FlowSpec.from_dict(self.to_dict())
        spec.mode = mode
        if name is not None:
            spec.name = name
        return spec

    # -- graph -------------------------------------------------------------

    @property
    def graph(self) -> nx.DiGraph:
        if self._graph is None:
            g = nx.DiGraph()
            for n in self.nodes:
                g.add_node(n.name, spec=n)
            for n in self.nodes:
                for p in n.parents:
                    g.add_edge(p, n.name)
            self._graph = g
        return self._graph

    def node(self, name: str) -> NodeSpec:
        return self.graph.nodes[name]["spec"]

    def host(self, name: str) -> str:
        """Host of a node (or of a multi-target address "a+b", whose
        targets always share a host)."""
        name = targets(name)[0]
        return CLIENT if name == SINK else self.node(name).host

    def parents(self, name: str) -> list[str]:
        return list(self.node(name).parents)

    def children(self, name: str) -> list[str]:
        return sorted(self.graph.successors(name))

    def sources(self) -> list[str]:
        return [n.name for n in self.nodes if n.kind == SOURCE]

    def leaves(self) -> list[str]:
        return [n.name for n in self.nodes if n.kind == ENGINE and not self.children(n.name)]

    def on_host(self, host: str) -> list[str]:
        return [n.name for n in self.nodes if n.host == host]

    def is_network_edge(self, parent: str, child: str) -> bool:
        """Whether data on this edge crosses the client <-> remote link."""
        return (self.host(parent) == REMOTE) != (self.host(child) == REMOTE)

    # -- segment mode -------------------------------------------------------

    def groups(self, name: str) -> dict[str, list[str]]:
        """Fan-out groups of a node: {"local": [...], "remote": [...]}, by
        whether the edge crosses the network. Sources always group their
        children; engines only when they have more than one child."""
        children = self.children(name)
        if self.node(name).kind != SOURCE and len(children) <= 1:
            return {}
        out = {}
        for c in children:
            kind = "remote" if self.is_network_edge(name, c) else "local"
            out.setdefault(kind, []).append(c)
        return out

    def is_head(self, name: str) -> bool:
        return bool(self.groups(name))

    # -- segment mode, per-branch tokens --------------------------------------

    def branch_head(self, name: str) -> bool:
        """Heads: Sources, engines with several children, and (implicitly)
        any engine with a child across the network, so that a network edge
        always leaves from a fan-out."""
        n = self.node(name)
        children = self.children(name)
        if n.kind == SOURCE:
            return True
        return len(children) > 1 or any(self.is_network_edge(name, c) for c in children)

    def branch_stages(self, head: str, child: str) -> int:
        """Engines a branch's frame runs through: from `child` down to the
        leaf or next head (inclusive), longest path."""
        best = 0
        stack = [(child, 1)]
        while stack:
            node, depth = stack.pop()
            best = max(best, depth)
            if not self.branch_head(node):
                stack.extend((c, depth + 1) for c in self.children(node))
        return best

    def segment_crosses(self, name: str, kind: str) -> bool:
        """Whether a group's segment (down to the next head or leaf) crosses
        the network anywhere."""
        stack = [(name, c) for c in self.groups(name).get(kind, [])]
        while stack:
            parent, child = stack.pop()
            if self.is_network_edge(parent, child):
                return True
            if not self.is_head(child):
                stack.extend((child, g) for g in self.children(child))
        return False

    def upstream_sources(self, name: str) -> list[str]:
        return sorted(s for s in nx.ancestors(self.graph, name) if self.node(s).kind == SOURCE)

    def validate(self):
        if self.mode not in MODES:
            raise ValueError(f"flow {self.name}: unknown mode {self.mode!r}")
        if any(ch in self.name for ch in _RESERVED):
            raise ValueError(f"flow name {self.name!r} may not contain {_RESERVED!r}")
        names = [n.name for n in self.nodes]
        if len(set(names)) != len(names):
            raise ValueError(f"flow {self.name}: duplicate node names")
        for n in self.nodes:
            if not n.name or any(ch in n.name for ch in _RESERVED):
                raise ValueError(f"node name {n.name!r} may not contain {_RESERVED!r}")
            if n.kind not in (SOURCE, ENGINE):
                raise ValueError(f"node {n.name}: unknown kind {n.kind!r}")
            if n.host not in HOSTS:
                raise ValueError(f"node {n.name}: unknown host {n.host!r}")
            if n.kind == SOURCE and n.parents:
                raise ValueError(f"source {n.name} cannot have parents")
            if n.kind == ENGINE and not n.parents:
                raise ValueError(f"engine {n.name} needs at least one parent")
            if n.kind == ENGINE and n.host == CLIENT:
                raise ValueError(f"engine {n.name} must run on {LOCAL!r} or {REMOTE!r}")
            for p in n.parents:
                if p not in names:
                    raise ValueError(f"node {n.name}: unknown parent {p!r}")
            if not 0 < n.pass_rate <= 1:
                raise ValueError(f"node {n.name}: pass_rate must be in (0, 1]")
        if not nx.is_directed_acyclic_graph(self.graph):
            raise ValueError(f"flow {self.name}: graph has a cycle")
        if not self.sources():
            raise ValueError(f"flow {self.name}: no sources")
        for s in self.sources():
            if not self.children(s):
                raise ValueError(f"source {s} feeds nothing")


def channel(flow: str, parent: str, child: str) -> str:
    """Transport channel carrying data on one edge of one flow."""
    return f"{flow}|{parent}|{child}"


def targets(to: str) -> list[str]:
    """Nodes addressed by `to` ("a" or "a+b+c")."""
    return to.split("+")


def parse_channel(name: str) -> tuple[str, str, str]:
    flow, parent, child = name.split("|")
    return flow, parent, child
