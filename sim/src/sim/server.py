"""Engine host servers.

The **local server** manages the engines that share a host with the client
(it talks to the client over iceoryx2). The **remote server** manages the
engines on the remote host (it talks to the client over QUIC). Both receive
every flow spec from the client and create a `FlowManager` for it, which
instantiates the nodes placed on this server's host. Edges between two nodes
on the same server are delivered in-process; every other edge goes to the
client, whose FlowRouter forwards it (to the other server, a Source or the
result sink).

The remote server is also the remote-side network edge agent: it pings the
client for RTT, times incoming frames for T, and applies both to demands
(see edge.py).

Run: ``python -m sim.server local --iox PREFIX`` or
``python -m sim.server remote --quic HOST:PORT --cert C --key K --iox PREFIX``.
"""

import argparse
import asyncio
import itertools
import logging
import signal
import traceback

from . import messages as m
from .edge import EdgeAgent
from .engine import EngineRegistry, InlineEngine, ProcessEngine
from .flow import LOCAL, REMOTE, FlowSpec, channel, parse_channel, targets
from .messages import Item, decode_control, encode_control
from .metrics import node_report
from .nodes import Node, make_node, returns_for_replaced
from .transport import CONTROL, IceoryxTransport, QuicTransport, Transport

log = logging.getLogger("sim.server")


class FlowManager:
    """The part of one flow that runs on this server's host."""

    def __init__(self, server: "Server", spec: FlowSpec):
        self.server = server
        self.spec = spec
        self.host = server.host
        self.loop = asyncio.get_running_loop()
        self.names = spec.on_host(self.host)
        self.nodes: dict[str, Node] = {name: make_node(spec, name, self) for name in self.names}
        self.engines = {}
        self.t_mark = self.loop.time()
        self.stopped = False

    async def start(self):
        for name in self.names:
            ns = self.spec.node(name)
            seed = f"{self.spec.seed}/{self.spec.name}/{name}"
            if self.spec.params.engine_process:
                engine = ProcessEngine(
                    ns, seed, self.server.registry.endpoint,
                    f"{self.spec.name}.{name}.{next(self.server.engine_ids)}",
                    self.server.registry,
                )
            else:
                engine = InlineEngine(ns, seed)
            self.engines[name] = engine
        await asyncio.gather(*(e.start() for e in self.engines.values()))
        for node in self.nodes.values():
            node.start()

    def mark(self):
        self.t_mark = self.loop.time()

    async def stop(self) -> dict:
        t_stop = self.loop.time()
        self.stopped = True
        for node in self.nodes.values():
            node.stop()
        reports = {name: node_report(node, self.t_mark, t_stop) for name, node in self.nodes.items()}
        await asyncio.gather(*(e.close() for e in self.engines.values()), return_exceptions=True)
        return reports

    # -- NodeIO ----------------------------------------------------------------

    def now(self) -> float:
        return self.loop.time()

    def call_at(self, when, callback):
        return self.loop.call_at(when, callback)

    def send_data(self, frm: str, to: str, item: Item):
        if self.spec.host(to) == self.host:
            for t in targets(to):
                self.loop.call_soon(self._deliver_data, frm, t, item)
        else:
            # One copy, even for several targets ("a+b"): the far host
            # delivers it to each.
            self.server.uplink(item.encode(), channel(self.spec.name, frm, to))

    def send_control(self, msg: dict):
        if self.spec.host(msg["to"]) == self.host:
            self.loop.call_soon(self._deliver_control, msg)
        else:
            if self.server.agent is not None:
                msg = self.server.agent.outgoing(self.spec, msg)
            self.server.uplink(encode_control(msg), CONTROL)

    def run_engine(self, node, inputs, done):
        task = self.loop.create_task(self.engines[node].run([i.payload for i in inputs]))

        def finished(t: asyncio.Task):
            if t.cancelled():
                return
            error = t.exception()  # always retrieve it, even when stopping
            if self.stopped:
                return
            if error is not None:
                log.warning("engine %s failed: %r", node, error)
                done(None)
            else:
                done(t.result())

        task.add_done_callback(finished)

    # -- from the client -------------------------------------------------------

    def _deliver_data(self, frm, to, item):
        if not self.stopped:
            self.nodes[to].on_data(frm, item)

    def _deliver_control(self, msg):
        if not self.stopped:
            self.nodes[msg["to"]].on_control(msg)

    def on_data(self, frm: str, to: str, raw: bytes):
        if self.stopped:
            return
        item = None
        for t in targets(to):
            node = self.nodes.get(t)
            if node is not None:
                item = item or Item.decode(raw)
                node.on_data(frm, item)

    def on_control(self, msg: dict):
        if self.server.agent is not None:
            msg = self.server.agent.incoming(self.spec, msg)
        node = self.nodes.get(msg["to"])
        if node is not None and not self.stopped:
            node.on_control(msg)


class Server:
    def __init__(self, host: str, transport: Transport, endpoint: str, iox_prefix: str,
                 ping_interval: float = 0.1, link_timeout: float = 0.5):
        self.host = host
        self.transport = transport
        self.endpoint = endpoint
        self.registry = EngineRegistry(f"{iox_prefix}/{host}/engines")
        self.engine_ids = itertools.count()
        self.flows: dict[str, FlowManager] = {}
        self.client_id: str | None = None
        self.agent = EdgeAgent(side_is_remote=True) if host == REMOTE else None
        self.ping_interval = ping_interval
        self._ping_ids = itertools.count()
        self.link_up = True
        self.link_timeout = link_timeout
        self.last_pong = 0.0
        if self.agent is not None:
            transport.on_frame = lambda _cid, chan, size, elapsed: self.agent.on_frame(chan, size, elapsed)
        transport.on_replace = self._on_send_replaced

    async def run(self, ready: asyncio.Event | None = None):
        if not await self.transport.bind(self.endpoint):
            raise RuntimeError(f"{self.host} server cannot bind {self.endpoint}")
        await self.registry.start()
        log.info("%s server listening on %s", self.host, self.endpoint)
        if ready is not None:
            ready.set()
        tasks = []
        if self.agent is not None:
            tasks.append(asyncio.create_task(self._ping_loop()))
        try:
            while True:
                client_id = await self.transport.accept()
                log.info("client %s connected", client_id)
                self.client_id = client_id
                if self.host == REMOTE:
                    self._set_link(True)
                tasks.append(asyncio.create_task(self._serve(client_id)))
        finally:
            for t in tasks:
                t.cancel()
            for fm in list(self.flows.values()):
                await fm.stop()
            await self.registry.close()
            await self.transport.close()

    def _transmit_pct(self, chan: str) -> float:
        fm = self.flows.get(chan.split("|")[0])
        return fm.spec.params.transmit_pct if fm is not None else 90

    def _on_send_replaced(self, _cid, chan: str, raw: bytes):
        """A frame was replaced in the sender slot before it left."""
        flow_name, parent, _ = parse_channel(chan)
        fm = self.flows.get(flow_name)
        if fm is None or fm.stopped:
            return
        for msg in returns_for_replaced(fm.spec, parent, raw):
            fm.send_control(msg)

    def _set_link(self, up: bool):
        """The client <-> remote link went down or came back."""
        if up == self.link_up:
            return
        self.link_up = up
        log.info("remote link %s for flows", "up" if up else "down")
        for fm in self.flows.values():
            if not fm.stopped:
                for node in fm.nodes.values():
                    node.on_link(up)

    def uplink(self, payload: bytes, chan: str) -> bool:
        if self.client_id is None:
            return False
        return self.transport.send_srv_nowait(payload, self.client_id, chan)

    async def _ping_loop(self):
        loop = asyncio.get_running_loop()
        last_log = loop.time()
        while True:
            await asyncio.sleep(self.ping_interval)
            self.uplink(encode_control({"t": m.PING, "id": next(self._ping_ids), "ts": loop.time()}), CONTROL)
            if self.last_pong and loop.time() - self.last_pong > self.link_timeout:
                self._set_link(False)
            if loop.time() - last_log >= 2.0:
                last_log = loop.time()
                t = ", ".join(
                    f"{c}={self.agent.t_for(c, self._transmit_pct(c)) * 1000:.1f}ms"
                    for c in self.agent.transmit
                )
                log.info("edge: rtt=%.1fms T: %s", self.agent.rtt.value * 1000, t or "-")

    async def _serve(self, client_id: str):
        while True:
            try:
                chan, payload = await self.transport.recv_any_srv(client_id)
            except ConnectionError:
                log.info("client %s disconnected", client_id)
                if self.host == REMOTE:
                    self._set_link(False)
                return
            try:
                if chan == CONTROL:
                    await self._on_control(decode_control(payload))
                else:
                    flow, frm, to = parse_channel(chan)
                    fm = self.flows.get(flow)
                    if fm is not None:
                        fm.on_data(frm, to, payload)
            except Exception:
                log.error("error handling %s message:\n%s", chan, traceback.format_exc())

    def _reply(self, msg: dict):
        self.uplink(encode_control(msg), CONTROL)

    async def _on_control(self, msg: dict):
        t = msg["t"]
        if t == m.PING:
            self._reply({"t": m.PONG, "ts": msg["ts"]})
        elif t == m.PONG:
            if self.agent is not None:
                self.agent.rtt.update(asyncio.get_running_loop().time() - msg["ts"])
                self.last_pong = asyncio.get_running_loop().time()
                self._set_link(True)
        elif t == m.FLOW:
            asyncio.create_task(self._create_flow(FlowSpec.from_dict(msg["spec"])))
        elif t == m.LINK:
            self._set_link(msg["up"])
        elif t == m.MARK:
            fm = self.flows.get(msg["f"])
            if fm is not None:
                fm.mark()
        elif t == m.STOP:
            asyncio.create_task(self._stop_flow(msg["f"]))
        else:
            fm = self.flows.get(msg.get("f"))
            if fm is not None:
                fm.on_control(msg)

    async def _stop_flow(self, name: str):
        fm = self.flows.pop(name, None)
        reports = await fm.stop() if fm is not None else {}
        self._reply({"t": m.REPORT, "f": name, "host": self.host, "nodes": reports})

    async def _create_flow(self, spec: FlowSpec):
        try:
            spec.validate()
            if spec.name in self.flows:
                await self.flows.pop(spec.name).stop()
            fm = FlowManager(self, spec)
            self.flows[spec.name] = fm
            await fm.start()
            self._reply({"t": m.READY, "f": spec.name, "host": self.host})
        except Exception as e:
            log.error("cannot create flow %s:\n%s", spec.name, traceback.format_exc())
            self.flows.pop(spec.name, None)
            self._reply({"t": m.ERROR, "f": spec.name, "host": self.host, "error": repr(e)})


def main():
    parser = argparse.ArgumentParser(description="engine host server")
    parser.add_argument("host", choices=[LOCAL, REMOTE])
    parser.add_argument("--iox", required=True, help="iceoryx2 service prefix")
    parser.add_argument("--quic", help="remote: HOST:PORT to bind")
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--idle-timeout", type=float, default=2.0)
    parser.add_argument("--ping-ms", type=float, default=100.0)
    parser.add_argument("--log", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log, format=f"%(asctime)s {args.host} %(levelname)s %(message)s")

    if args.host == LOCAL:
        transport, endpoint = IceoryxTransport(), f"{args.iox}/local"
    else:
        if not (args.quic and args.cert and args.key):
            parser.error("remote needs --quic, --cert and --key")
        transport = QuicTransport(server_creds=(args.cert, args.key), idle_timeout=args.idle_timeout)
        endpoint = args.quic
    server = Server(args.host, transport, endpoint, args.iox, ping_interval=args.ping_ms / 1000)

    async def run():
        task = asyncio.create_task(server.run())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


if __name__ == "__main__":
    main()
