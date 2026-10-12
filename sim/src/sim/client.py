"""Client: owns the Sources and the result sink, and routes between servers.

The client connects to the local server over iceoryx2 and to the remote
server over QUIC. Creating a flow sends the same flow spec to both servers;
each builds the part of the graph placed on its host. Whenever data or a
control message has to go between hosts (local server <-> remote server, or
either server <-> a Source or the sink), it goes through the client's
`FlowRouter`, which forwards data payloads without decoding them.

The router is also the local-side network edge agent for the QUIC link: it
pings the remote server for RTT, times frames arriving from the remote host
for T, and applies both to demands crossing the link (see edge.py).

The remote connection is re-established after an outage; nothing is
replayed (slots start empty, leases expire, demands restart the flow).
"""

import asyncio
import itertools
import logging
from dataclasses import dataclass, field

from . import messages as m
from .edge import EdgeAgent
from .flow import CLIENT, LOCAL, REMOTE, SINK, FlowSpec, channel, parse_channel
from .messages import Item, decode_control, encode_control
from .metrics import Sink, node_report, summarize
from .nodes import Node, make_node, returns_for_replaced
from .transport import CONTROL, IceoryxTransport, QuicTransport, Transport

log = logging.getLogger("sim.client")


class _Link:
    """Client side of one server connection, reconnecting when it drops."""

    def __init__(self, name: str, make_transport, endpoint: str, on_message, retry: float = 0.25):
        self.name = name
        self.make_transport = make_transport
        self.endpoint = endpoint
        self.on_message = on_message
        self.retry = retry
        self.transport: Transport | None = None
        self.connected = asyncio.Event()
        self.on_frame = None
        self.on_replace = None
        self.on_state = None
        self.disconnects = 0
        self._task = None
        self._client_id = None

    async def start(self, timeout: float = 10.0):
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self.connected.wait(), timeout)

    async def _run(self):
        while True:
            transport = self.make_transport(self._client_id)
            self._client_id = transport.client_id  # reconnect with the same id
            transport.on_frame = self.on_frame
            transport.on_replace = self.on_replace
            if not await transport.connect(self.endpoint):
                await transport.close()
                await asyncio.sleep(self.retry)
                continue
            self.transport = transport
            self.connected.set()
            log.info("%s link up", self.name)
            if self.on_state is not None:
                self.on_state(self.name, True)
            try:
                while True:
                    chan, payload = await transport.recv_any_cli()
                    try:
                        self.on_message(self.name, chan, payload)
                    except Exception:
                        log.exception("error handling %s message from %s", chan, self.name)
            except ConnectionError:
                pass
            self.connected.clear()
            self.transport = None
            self.disconnects += 1
            if self.on_state is not None:
                self.on_state(self.name, False)
            log.warning("%s link down; reconnecting", self.name)
            await transport.close()
            await asyncio.sleep(self.retry)

    def send(self, payload: bytes, chan: str) -> bool:
        t = self.transport
        return t is not None and t.send_cli_nowait(payload, chan)

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self.transport is not None:
            await self.transport.close()


@dataclass
class ClientFlow:
    spec: FlowSpec
    nodes: dict[str, Node]
    sink: Sink
    ready: dict[str, asyncio.Future] = field(default_factory=dict)
    reports: dict[str, asyncio.Future] = field(default_factory=dict)
    t_mark: float = 0.0
    stopped: bool = False


class _FlowIO:
    """NodeIO for the client-side nodes (Sources) of one flow."""

    def __init__(self, router: "FlowRouter", spec: FlowSpec):
        self.router = router
        self.spec = spec
        self.loop = asyncio.get_running_loop()

    def now(self):
        return self.loop.time()

    def call_at(self, when, callback):
        return self.loop.call_at(when, callback)

    def send_data(self, frm, to, item: Item):
        self.router.route_data(self.spec, frm, to, item.encode(), item)

    def send_control(self, msg):
        self.router.route_control(self.spec, msg, came_from=CLIENT)

    def run_engine(self, node, inputs, done):
        raise RuntimeError("no engines run on the client")


class FlowRouter:
    """Routes every flow's data and control between the client's own nodes,
    the local server and the remote server."""

    def __init__(self, client: "Client"):
        self.client = client
        self.flows: dict[str, ClientFlow] = {}
        self.agent = EdgeAgent(side_is_remote=False)
        self.loop = asyncio.get_running_loop()
        self.forwarded = {LOCAL: 0, REMOTE: 0}
        self.dropped = {LOCAL: 0, REMOTE: 0}
        self.remote_up = True  # client <-> remote link, as nodes see it
        self.last_pong = self.loop.time()

    def link(self, host: str) -> _Link:
        return self.client.links[host]

    def _send(self, host: str, payload: bytes, chan: str):
        if self.link(host).send(payload, chan):
            self.forwarded[host] += 1
        else:
            self.dropped[host] += 1

    # -- data -------------------------------------------------------------------

    def route_data(self, spec: FlowSpec, frm: str, to: str, raw: bytes, item: Item | None = None):
        host = spec.host(to)
        flow = self.flows.get(spec.name)
        if host == CLIENT:
            if flow is None or flow.stopped:
                return
            item = item or Item.decode(raw)
            if to == SINK:
                flow.sink.on_result(frm, item)
            else:
                self.loop.call_soon(flow.nodes[to].on_data, frm, item)
        else:
            self._send(host, raw, channel(spec.name, frm, to))

    # -- control ----------------------------------------------------------------

    def route_control(self, spec: FlowSpec, msg: dict, came_from: str):
        if came_from == REMOTE:
            msg = self.agent.incoming(spec, msg)
        host = spec.host(msg["to"])
        if host == CLIENT:
            flow = self.flows.get(spec.name)
            if flow is not None and not flow.stopped:
                node = flow.nodes.get(msg["to"])
                if node is not None:
                    self.loop.call_soon(node.on_control, msg)
            return
        if host == REMOTE:
            msg = self.agent.outgoing(spec, msg)
        self._send(host, encode_control(msg), CONTROL)

    def on_send_replaced(self, chan: str, raw: bytes):
        """A frame was replaced in the QUIC sender slot before it left."""
        flow_name, parent, _ = parse_channel(chan)
        flow = self.flows.get(flow_name)
        if flow is None or flow.stopped:
            return
        for msg in returns_for_replaced(flow.spec, parent, raw):
            self.route_control(flow.spec, msg, came_from=CLIENT)

    def set_remote_link(self, up: bool):
        """Tell every node (client-side ones directly, the local server by
        message) that the client <-> remote link went down or came back."""
        if up == self.remote_up:
            return
        self.remote_up = up
        log.info("remote link %s for flows", "up" if up else "down")
        for flow in self.flows.values():
            for node in flow.nodes.values():
                node.on_link(up)
        self._send(LOCAL, encode_control({"t": m.LINK, "up": up}), CONTROL)

    # -- messages from servers ------------------------------------------------

    def on_message(self, host: str, chan: str, payload: bytes):
        if chan != CONTROL:
            flow_name, frm, to = parse_channel(chan)
            flow = self.flows.get(flow_name)
            if flow is not None:
                self.route_data(flow.spec, frm, to, payload)
            return
        msg = decode_control(payload)
        t = msg["t"]
        if t == m.PING:
            self._send(host, encode_control({"t": m.PONG, "ts": msg["ts"]}), CONTROL)
        elif t == m.PONG:
            if host == REMOTE:
                self.agent.rtt.update(self.loop.time() - msg["ts"])
                self.last_pong = self.loop.time()
                if self.client.links[REMOTE].connected.is_set():
                    self.set_remote_link(True)
        elif t in (m.READY, m.ERROR, m.REPORT):
            flow = self.flows.get(msg["f"])
            if flow is None:
                return
            futures = flow.reports if t == m.REPORT else flow.ready
            fut = futures.get(msg["host"])
            if fut is not None and not fut.done():
                if t == m.ERROR:
                    fut.set_exception(RuntimeError(f"{msg['host']}: {msg['error']}"))
                else:
                    fut.set_result(msg)
        else:
            flow = self.flows.get(msg.get("f"))
            if flow is not None:
                self.route_control(flow.spec, msg, came_from=host)


class Client:
    def __init__(
        self,
        local_endpoint: str,
        remote_endpoint: str,
        quic_idle_timeout: float = 2.0,
        congestion_control: str = "cubic",
        ping_interval: float = 0.1,
        link_timeout: float = 0.5,
    ):
        self.local_endpoint = local_endpoint
        self.remote_endpoint = remote_endpoint
        self.quic_idle_timeout = quic_idle_timeout
        self.congestion_control = congestion_control
        self.ping_interval = ping_interval
        self.link_timeout = link_timeout
        self.links: dict[str, _Link] = {}
        self.router: FlowRouter | None = None
        self._ping_task = None
        self._ping_ids = itertools.count()

    async def start(self, timeout: float = 15.0):
        self.router = FlowRouter(self)
        self.links[LOCAL] = _Link(
            LOCAL, lambda cid: IceoryxTransport(client_id=cid), self.local_endpoint,
            self.router.on_message,
        )
        remote = _Link(
            REMOTE,
            lambda cid: QuicTransport(
                client_id=cid, idle_timeout=self.quic_idle_timeout,
                congestion_control=self.congestion_control,
            ),
            self.remote_endpoint,
            self.router.on_message,
        )
        remote.on_frame = lambda _cid, chan, size, elapsed: self.router.agent.on_frame(chan, size, elapsed)
        remote.on_replace = lambda _cid, chan, raw: self.router.on_send_replaced(chan, raw)
        def remote_state(_name, up):
            if up:
                self.router.last_pong = asyncio.get_running_loop().time()  # restart the silence clock
            self.router.set_remote_link(up)

        remote.on_state = remote_state
        self.links[REMOTE] = remote
        await asyncio.gather(*(link.start(timeout) for link in self.links.values()))
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def _ping_loop(self):
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self.ping_interval)
            msg = {"t": m.PING, "id": next(self._ping_ids), "ts": loop.time()}
            self.links[REMOTE].send(encode_control(msg), CONTROL)
            # A silent link is down long before QUIC's idle timeout says so.
            if loop.time() - self.router.last_pong > self.link_timeout:
                self.router.set_remote_link(False)

    async def close(self):
        if self._ping_task is not None:
            self._ping_task.cancel()
        for link in self.links.values():
            await link.close()

    # -- flows --------------------------------------------------------------------

    async def create_flow(self, spec: FlowSpec, timeout: float = 30.0):
        """Send the spec to both servers, wait until both have built their
        part, then start the Sources."""
        spec.validate()
        router = self.router
        if spec.name in router.flows:
            raise ValueError(f"flow {spec.name} already exists")
        io = _FlowIO(router, spec)
        loop = asyncio.get_running_loop()
        nodes = {name: make_node(spec, name, io) for name in spec.on_host(CLIENT)}
        sink = Sink(spec, loop.time, lambda msg: router.route_control(spec, msg, came_from=CLIENT))
        flow = ClientFlow(spec, nodes, sink)
        flow.ready = {LOCAL: loop.create_future(), REMOTE: loop.create_future()}
        router.flows[spec.name] = flow
        payload = encode_control({"t": m.FLOW, "spec": spec.to_dict()})
        for host in (LOCAL, REMOTE):
            if not self.links[host].send(payload, CONTROL):
                raise ConnectionError(f"{host} server unreachable")
        try:
            await asyncio.wait_for(asyncio.gather(*flow.ready.values()), timeout)
        except BaseException:
            router.flows.pop(spec.name, None)
            raise
        for node in nodes.values():
            node.start()
        flow.t_mark = loop.time()
        return flow

    def mark(self, name: str):
        """Start the measurement window now (after warmup)."""
        flow = self.router.flows[name]
        flow.t_mark = asyncio.get_running_loop().time()
        payload = encode_control({"t": m.MARK, "f": name})
        for host in (LOCAL, REMOTE):
            self.links[host].send(payload, CONTROL)

    async def stop_flow(self, name: str, timeout: float = 15.0) -> dict:
        """Stop the flow everywhere and return its summary metrics."""
        router = self.router
        flow = router.flows[name]
        loop = asyncio.get_running_loop()
        t_stop = loop.time()
        flow.stopped = True
        for node in flow.nodes.values():
            node.stop()
        flow.reports = {LOCAL: loop.create_future(), REMOTE: loop.create_future()}
        payload = encode_control({"t": m.STOP, "f": name})
        reports = {name_: node_report(n, flow.t_mark, t_stop) for name_, n in flow.nodes.items()}
        missing = []
        for host in (LOCAL, REMOTE):
            # The remote may be mid-outage: wait for the link to come back.
            deadline = loop.time() + timeout
            while not self.links[host].send(payload, CONTROL):
                if loop.time() > deadline:
                    break
                await asyncio.sleep(0.1)
        for host, fut in flow.reports.items():
            try:
                msg = await asyncio.wait_for(fut, timeout)
                reports.update(msg["nodes"])
            except TimeoutError:
                missing.append(host)
        router.flows.pop(name, None)
        summary = summarize(flow.spec, flow.sink, reports, flow.t_mark, t_stop)
        summary["missing_hosts"] = missing
        summary["remote_disconnects"] = self.links[REMOTE].disconnects
        return summary
