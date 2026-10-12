"""Dummy engines: a fixed computation the scheduler does not control.

An engine takes the run's inputs, works for a random service time, and
returns an output payload or None (filter rejection). Engines run either
inline (an asyncio sleep inside the server, for tests) or as separate worker
processes connected to their server over iceoryx2 (the default), so the
server only sees them as black boxes.

Worker entry point: ``python -m sim.engine ENDPOINT CLIENT_ID SPEC_JSON``.
"""

import argparse
import asyncio
import json
import math
import random
import struct
import sys

from .flow import NodeSpec, Service
from .transport import IceoryxTransport

_REJECT = b"\x00"
_OUTPUT = b"\x01"
_COUNT = struct.Struct("<I")


def sample_ms(service: Service, rng: random.Random) -> float:
    mean, cv = service.mean_ms, service.cv
    if service.dist == "const":
        x = mean
    elif service.dist == "uniform":
        x = rng.uniform(mean * (1 - cv), mean * (1 + cv))
    elif service.dist == "normal":
        x = rng.gauss(mean, mean * cv)
    elif service.dist == "exp":
        x = rng.expovariate(1 / mean) if mean > 0 else 0.0
    elif service.dist == "lognormal":
        if cv <= 0:
            x = mean
        else:
            sigma2 = math.log(1 + cv * cv)
            x = rng.lognormvariate(math.log(mean) - sigma2 / 2, math.sqrt(sigma2))
    else:
        raise ValueError(f"unknown service distribution {service.dist!r}")
    return max(x, service.min_ms, 0.0)


class Behavior:
    """What a dummy engine does per run: sleep, then maybe reject."""

    def __init__(self, spec: NodeSpec, seed: str):
        self.spec = spec
        self.rng = random.Random(seed)
        self.output = bytes(spec.output_bytes)

    def plan(self) -> tuple[float, bool]:
        """(service seconds, passes filter)"""
        return sample_ms(self.spec.service, self.rng) / 1000, self.rng.random() < self.spec.pass_rate


class InlineEngine:
    def __init__(self, spec: NodeSpec, seed: str):
        self.behavior = Behavior(spec, seed)

    async def start(self):
        pass

    async def run(self, inputs: list[bytes]) -> bytes | None:
        seconds, passes = self.behavior.plan()
        await asyncio.sleep(seconds)
        return self.behavior.output if passes else None

    async def close(self):
        pass


def pack_inputs(inputs: list[bytes]) -> bytes:
    out = [_COUNT.pack(len(inputs))]
    for x in inputs:
        out.append(_COUNT.pack(len(x)))
        out.append(x)
    return b"".join(out)


def unpack_inputs(raw: bytes) -> list[bytes]:
    (n,) = _COUNT.unpack_from(raw)
    pos = _COUNT.size
    inputs = []
    for _ in range(n):
        (length,) = _COUNT.unpack_from(raw, pos)
        pos += _COUNT.size
        inputs.append(raw[pos:pos + length])
        pos += length
    return inputs


class ProcessEngine:
    """Engine in a separate worker process, reached over iceoryx2 through
    the server's engine endpoint (one channel each way, one run at a time)."""

    def __init__(self, spec: NodeSpec, seed: str, endpoint: str, client_id: str, registry):
        self.spec = spec
        self.seed = seed
        self.endpoint = endpoint
        self.client_id = client_id
        self.registry = registry  # EngineRegistry of the server
        self.proc = None

    async def start(self):
        connected = self.registry.expect(self.client_id)
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "sim.engine", self.endpoint, self.client_id,
            json.dumps({"spec": _spec_dict(self.spec), "seed": self.seed}),
        )
        await asyncio.wait_for(connected, 15)

    async def run(self, inputs: list[bytes]) -> bytes | None:
        transport = self.registry.transport
        if not await transport.send_srv(pack_inputs(inputs), self.client_id):
            raise ConnectionError(f"engine {self.client_id} is gone")
        reply = await transport.recv_srv(self.client_id)
        return reply[1:] if reply[:1] == _OUTPUT else None

    async def close(self):
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()


def _spec_dict(spec: NodeSpec) -> dict:
    from dataclasses import asdict

    return asdict(spec)


class EngineRegistry:
    """Server-side iceoryx2 endpoint that engine workers connect to."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.transport = IceoryxTransport()
        self._waiting: dict[str, asyncio.Future] = {}
        self._task = None

    async def start(self):
        if not await self.transport.bind(self.endpoint):
            raise RuntimeError(f"cannot bind engine endpoint {self.endpoint}")
        self._task = asyncio.create_task(self._accept())

    def expect(self, client_id: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._waiting[client_id] = fut
        return fut

    async def _accept(self):
        while True:
            client_id = await self.transport.accept()
            fut = self._waiting.pop(client_id, None)
            if fut is not None and not fut.done():
                fut.set_result(None)

    async def close(self):
        if self._task is not None:
            self._task.cancel()
        await self.transport.close()


async def worker(endpoint: str, client_id: str, config: dict):
    spec = NodeSpec(**config["spec"])
    behavior = Behavior(spec, config["seed"])
    transport = IceoryxTransport(client_id=client_id)
    if not await transport.connect(endpoint):
        raise SystemExit(f"engine {client_id}: cannot connect to {endpoint}")
    while True:
        try:
            raw = await transport.recv_cli()
        except ConnectionError:
            return
        unpack_inputs(raw)  # a real engine would compute on these
        seconds, passes = behavior.plan()
        await asyncio.sleep(seconds)
        await transport.send_cli(_OUTPUT + behavior.output if passes else _REJECT)


def main():
    parser = argparse.ArgumentParser(description="dummy engine worker")
    parser.add_argument("endpoint")
    parser.add_argument("client_id")
    parser.add_argument("config")
    args = parser.parse_args()
    try:
        asyncio.run(worker(args.endpoint, args.client_id, json.loads(args.config)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
