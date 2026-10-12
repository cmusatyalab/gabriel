"""Network emulation for the client <-> remote-host link.

Backends:
    netns   (default; needs root) The remote server runs in its own network
            namespace, connected to the client's namespace by a veth pair.
            Both veth ends carry a netem qdisc (delay, jitter, loss, rate,
            small queue limit), managed with pyroute2. `down` sets the link
            down; `blackhole` drops every packet (netem loss 100%).
    proxy   (no root) A userspace UDP relay between the client and the remote
            server applies the same model (serialization at `rate_mbit` with a
            `limit_pkts` drop-tail queue, then delay + jitter, loss, down).
            Coarser than netem (asyncio timer resolution), but good enough to
            exercise outages and bandwidth drops anywhere.
    direct  No shaping: the client talks to the remote server over loopback.

Per CLAUDE.md, keep the rate limiter's queue small (`limit_pkts`), or it adds
the bufferbloat being tested.

Proxy entry point: ``python -m sim.network proxy LISTEN TARGET PARAMS_JSON``;
it reads JSON lines on stdin: ``{"both"|"up"|"down": {param: value}}``.
"""

import argparse
import asyncio
import json
import random
import subprocess
import sys
from collections import deque
from dataclasses import asdict, dataclass, fields, replace

UP = "up"  # client -> remote
DOWN = "down"  # remote -> client
BOTH = "both"


@dataclass
class LinkParams:
    """One direction of the emulated link."""

    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    loss_pct: float = 0.0
    rate_mbit: float | None = None  # None = unlimited
    limit_pkts: int = 50  # queue in front of the rate limiter
    down: bool = False  # interface down (netns) / drop everything (proxy)
    blackhole: bool = False  # drop everything (netem loss 100%)

    def updated(self, changes: dict) -> "LinkParams":
        known = {f.name for f in fields(LinkParams)}
        bad = set(changes) - known
        if bad:
            raise ValueError(f"unknown link parameters {sorted(bad)}")
        return replace(self, **changes)

    def netem_kwargs(self) -> dict:
        """pyroute2 netem arguments (delay/jitter in microseconds)."""
        kw = {
            "delay": int(self.delay_ms * 1000),
            "jitter": int(self.jitter_ms * 1000),
            "loss": 100.0 if self.blackhole else self.loss_pct,
            "limit": self.limit_pkts,
        }
        if self.rate_mbit is not None:
            kw["rate"] = f"{max(1, int(self.rate_mbit * 1000))}kbit"
        return kw


@dataclass
class NetworkEvent:
    """A change to the link `at` seconds after the flows start."""

    at: float
    direction: str = BOTH
    changes: dict = None

    @classmethod
    def from_dict(cls, d: dict) -> "NetworkEvent":
        d = dict(d)
        at = float(d.pop("at"))
        direction = d.pop("direction", BOTH)
        if direction not in (UP, DOWN, BOTH):
            raise ValueError(f"event direction must be up, down or both, not {direction!r}")
        LinkParams().updated(d)  # validate names
        return cls(at, direction, d)


class Network:
    """Base backend: no shaping."""

    def __init__(self, up: LinkParams, down: LinkParams, port: int):
        self.params = {UP: up, DOWN: down}
        self.port = port

    @property
    def remote_bind(self) -> str:
        """Address the remote server binds."""
        return f"127.0.0.1:{self.port}"

    @property
    def remote_endpoint(self) -> str:
        """Address the client connects to."""
        return f"127.0.0.1:{self.port}"

    def remote_command(self, argv: list[str]) -> list[str]:
        """Wrap the remote server's command line (e.g. enter a namespace)."""
        return argv

    async def setup(self):
        pass

    async def apply(self, event: NetworkEvent):
        pass

    async def teardown(self):
        pass


class DirectNetwork(Network):
    pass


class NetnsNetwork(Network):
    """veth pair into a namespace for the remote server; netem on both ends."""

    def __init__(self, up, down, port, name: str = "gabsim", subnet: str = "10.77.0"):
        super().__init__(up, down, port)
        self.ns = f"{name}-remote"
        self.host_if = f"{name}-h"[:15]
        self.ns_if = f"{name}-n"[:15]
        self.host_ip = f"{subnet}.1"
        self.ns_ip = f"{subnet}.2"

    @property
    def remote_bind(self):
        return f"{self.ns_ip}:{self.port}"

    @property
    def remote_endpoint(self):
        return f"{self.ns_ip}:{self.port}"

    def remote_command(self, argv):
        return ["ip", "netns", "exec", self.ns, *argv]

    def _ipr(self, in_ns: bool):
        from pyroute2 import IPRoute

        return IPRoute(netns=self.ns) if in_ns else IPRoute()

    def _setup_sync(self):
        from pyroute2 import netns

        self._teardown_sync()
        netns.create(self.ns)
        with self._ipr(False) as ip:
            ip.link("add", ifname=self.host_if, kind="veth",
                    peer={"ifname": self.ns_if, "net_ns_fd": self.ns})
            idx = ip.link_lookup(ifname=self.host_if)[0]
            ip.addr("add", index=idx, address=self.host_ip, prefixlen=30)
            ip.link("set", index=idx, state="up")
            ip.tc("add", "netem", idx, handle=0x10000, **self.params[UP].netem_kwargs())
        with self._ipr(True) as ns:
            lo = ns.link_lookup(ifname="lo")[0]
            ns.link("set", index=lo, state="up")
            idx = ns.link_lookup(ifname=self.ns_if)[0]
            ns.addr("add", index=idx, address=self.ns_ip, prefixlen=30)
            ns.link("set", index=idx, state="up")
            ns.tc("add", "netem", idx, handle=0x10000, **self.params[DOWN].netem_kwargs())

    def _apply_sync(self, event: NetworkEvent):
        dirs = (UP, DOWN) if event.direction == BOTH else (event.direction,)
        for d in dirs:
            old = self.params[d]
            new = self.params[d] = old.updated(event.changes)
            with self._ipr(d == DOWN) as ip:
                ifname = self.ns_if if d == DOWN else self.host_if
                idx = ip.link_lookup(ifname=ifname)[0]
                if new.down != old.down:
                    ip.link("set", index=idx, state="down" if new.down else "up")
                ip.tc("replace", "netem", idx, handle=0x10000, **new.netem_kwargs())

    def _teardown_sync(self):
        from pyroute2 import netns

        try:
            with self._ipr(False) as ip:
                idx = ip.link_lookup(ifname=self.host_if)
                if idx:
                    ip.link("del", index=idx[0])
        except Exception:
            pass
        if self.ns in netns.listnetns():
            netns.remove(self.ns)

    async def setup(self):
        await asyncio.to_thread(self._setup_sync)

    async def apply(self, event):
        await asyncio.to_thread(self._apply_sync, event)

    async def teardown(self):
        await asyncio.to_thread(self._teardown_sync)


class ProxyNetwork(Network):
    """Userspace UDP relay process between the client and the remote server."""

    def __init__(self, up, down, port, listen_port: int):
        super().__init__(up, down, port)
        self.listen_port = listen_port
        self.proc = None

    @property
    def remote_endpoint(self):
        return f"127.0.0.1:{self.listen_port}"

    async def setup(self):
        params = json.dumps({UP: asdict(self.params[UP]), DOWN: asdict(self.params[DOWN])})
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "sim.network", "proxy",
            f"127.0.0.1:{self.listen_port}", f"127.0.0.1:{self.port}", params,
            stdin=subprocess.PIPE,
        )

    async def apply(self, event):
        line = json.dumps({event.direction: event.changes}) + "\n"
        self.proc.stdin.write(line.encode())
        await self.proc.stdin.drain()

    async def teardown(self):
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            await self.proc.wait()


def make_network(backend: str, up: LinkParams, down: LinkParams, port: int, **kw) -> Network:
    if backend == "netns":
        return NetnsNetwork(up, down, port, **kw)
    if backend == "proxy":
        return ProxyNetwork(up, down, port, listen_port=port + 1)
    if backend == "direct":
        return DirectNetwork(up, down, port)
    raise ValueError(f"unknown network backend {backend!r}")


# ---------------------------------------------------------------------------
# userspace proxy
# ---------------------------------------------------------------------------


class _Pipe:
    """One direction: drop-tail queue -> serializer at `rate` -> delay."""

    def __init__(self, params: LinkParams, deliver):
        self.p = params
        self.deliver = deliver
        self.queue: deque[bytes] = deque()
        self.busy = False
        self.last_arrival = 0.0
        self.loop = asyncio.get_running_loop()
        self.dropped = 0

    def push(self, datagram: bytes):
        if self.p.down or self.p.blackhole:
            self.dropped += 1
            return
        if self.p.rate_mbit is None:
            self._propagate(datagram)
            return
        if len(self.queue) >= self.p.limit_pkts:
            self.dropped += 1
            return
        self.queue.append(datagram)
        if not self.busy:
            self._next()

    def _next(self):
        if not self.queue:
            self.busy = False
            return
        self.busy = True
        datagram = self.queue.popleft()
        tx = len(datagram) * 8 / (self.p.rate_mbit * 1e6)

        def sent():
            self._propagate(datagram)
            self._next()

        self.loop.call_later(tx, sent)

    def _propagate(self, datagram: bytes):
        if self.p.down or self.p.blackhole or random.random() * 100 < self.p.loss_pct:
            self.dropped += 1
            return
        delay = (self.p.delay_ms + random.uniform(-1, 1) * self.p.jitter_ms) / 1000
        # Keep order (netem reorders with jitter; QUIC copes either way).
        arrival = max(self.loop.time() + max(delay, 0), self.last_arrival)
        self.last_arrival = arrival
        self.loop.call_at(arrival, self.deliver, datagram)


class _ServerSide(asyncio.DatagramProtocol):
    def __init__(self, proxy, client_addr):
        self.proxy = proxy
        self.client_addr = client_addr

    def datagram_received(self, data, addr):
        self.proxy.down_pipe(self.client_addr).push(data)


class _Proxy(asyncio.DatagramProtocol):
    def __init__(self, target, params):
        self.target = target
        self.params = params
        self.transport = None
        self.sessions = {}  # client addr -> (server transport, up pipe, down pipe)

    def connection_made(self, transport):
        self.transport = transport

    def down_pipe(self, client_addr):
        return self.sessions[client_addr][2]

    def datagram_received(self, data, addr):
        session = self.sessions.get(addr)
        if session is None:
            asyncio.ensure_future(self._open(addr, data))
            return
        if session[0] is not None:
            session[1].push(data)

    async def _open(self, addr, first):
        if addr in self.sessions:
            return
        self.sessions[addr] = (None, None, None)
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _ServerSide(self, addr), remote_addr=self.target
        )
        up = _Pipe(self.params[UP], transport.sendto)
        down = _Pipe(self.params[DOWN], lambda d, a=addr: self.transport.sendto(d, a))
        self.sessions[addr] = (transport, up, down)
        up.push(first)

    def update(self, direction, changes):
        dirs = (UP, DOWN) if direction == BOTH else (direction,)
        for d in dirs:
            self.params[d] = self.params[d].updated(changes)
            for _, up, down in self.sessions.values():
                pipe = up if d == UP else down
                if pipe is not None:
                    pipe.p = self.params[d]


def _split(endpoint):
    host, _, port = endpoint.rpartition(":")
    return host, int(port)


async def run_proxy(listen: str, target: str, params: dict):
    loop = asyncio.get_running_loop()
    params = {d: LinkParams(**params[d]) for d in (UP, DOWN)}
    proxy = _Proxy(_split(target), params)
    await loop.create_datagram_endpoint(lambda: proxy, local_addr=_split(listen))
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    while line := await reader.readline():
        for direction, changes in json.loads(line).items():
            proxy.update(direction, changes)
    await asyncio.Event().wait()  # stdin closed: keep relaying until killed


def main():
    parser = argparse.ArgumentParser(description="userspace link emulator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("proxy")
    p.add_argument("listen")
    p.add_argument("target")
    p.add_argument("params")
    args = parser.parse_args()
    try:
        asyncio.run(run_proxy(args.listen, args.target, json.loads(args.params)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
