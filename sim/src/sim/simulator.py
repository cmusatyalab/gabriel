"""Experiment runner.

Runs a list of experiments from a TOML file, one after another. Each
experiment is a set of flows (graph + placement), a network configuration
with optional timed events (outages, bandwidth/latency changes), and the
pacing modes to compare. For every (experiment, mode, repeat) the runner
sets up the network, starts the local and remote servers as processes,
connects a client, creates the flows, measures over [warmup, duration], and
records each flow's metrics.

    sim run experiments.toml [--out results] [--only NAME ...] [--backend B]
    sim check experiments.toml      # validate and list the expanded runs

Backends: netns (pyroute2 + netem, needs root: run with
``sudo .venv/bin/sim run ...``), proxy (userspace relay, no root), direct
(loopback, no shaping) and virtual (simulated time, no processes).

See experiments.toml in the repository root for the config format.
"""

import argparse
import asyncio
import copy
import csv
import datetime
import itertools
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path

from .client import Client
from .flow import MODES, FlowSpec, NodeSpec, Params
from .network import BOTH, LinkParams, NetworkEvent, make_network

log = logging.getLogger("sim")

BACKENDS = ("netns", "proxy", "direct", "virtual")


@dataclass
class NetworkConfig:
    backend: str = "netns"
    up: LinkParams = field(default_factory=LinkParams)
    down: LinkParams = field(default_factory=LinkParams)
    events: list[NetworkEvent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "NetworkConfig":
        d = dict(d)
        backend = d.pop("backend", "netns")
        if backend not in BACKENDS:
            raise ValueError(f"unknown backend {backend!r}; expected one of {BACKENDS}")
        events = [NetworkEvent.from_dict(e) for e in d.pop("events", [])]
        # `up` / `down` tables are per-direction overrides; a boolean `down`
        # is the link-down flag.
        up_over = d.pop("up") if isinstance(d.get("up"), dict) else {}
        down_over = d.pop("down") if isinstance(d.get("down"), dict) else {}
        base = LinkParams().updated(d)
        return cls(backend, base.updated(up_over), base.updated(down_over),
                   sorted(events, key=lambda e: e.at))


@dataclass
class Experiment:
    name: str
    flows: list[FlowSpec]
    network: NetworkConfig
    modes: list[str] = field(default_factory=lambda: list(MODES))
    duration: float = 20.0
    warmup: float = 3.0
    repeats: int = 1
    seed: int = 0
    quic_idle_timeout: float = 2.0
    congestion_control: str = "cubic"
    sweep: dict = field(default_factory=dict)  # the swept values that made it


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _set_dotted(d: dict, key: str, value):
    """Set a dotted path; numeric parts index lists (flow.0.node.1.service.mean_ms)."""
    parts = key.split(".")
    cur = d
    for i, part in enumerate(parts[:-1]):
        nxt_is_index = parts[i + 1].isdigit()
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur.setdefault(part, [] if nxt_is_index else {})
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _expand_sweeps(raw: dict) -> list[tuple[dict, dict]]:
    sweep = raw.get("sweep", {})
    if not sweep:
        return [(raw, {})]
    keys = list(sweep)
    out = []
    for values in itertools.product(*(sweep[k] for k in keys)):
        d = copy.deepcopy(raw)
        d.pop("sweep")
        point = dict(zip(keys, values))
        for k, v in point.items():
            _set_dotted(d, k, v)
        suffix = ",".join(f"{k.split('.')[-1]}={v}" for k, v in point.items())
        d["name"] = f"{raw['name']}[{suffix}]"
        out.append((d, point))
    return out


def _build(raw: dict, point: dict) -> Experiment:
    params = raw.get("params", {})
    known_params = {f.name for f in fields(Params)}
    bad = set(params) - known_params
    if bad:
        raise ValueError(f"experiment {raw['name']}: unknown params {sorted(bad)}")
    flows = []
    for f in raw.get("flow", []):
        nodes = [NodeSpec(**n) for n in f["node"]]
        fparams = Params(**{**params, **f.get("params", {})})
        spec = FlowSpec(f["name"], nodes, params=fparams, seed=raw.get("seed", 0))
        spec.validate()
        flows.append(spec)
    if not flows:
        raise ValueError(f"experiment {raw['name']}: no flows")
    modes = raw.get("modes", list(MODES))
    for mode in modes:
        if mode not in MODES:
            raise ValueError(f"experiment {raw['name']}: unknown mode {mode!r}")
    exp = Experiment(
        name=raw["name"],
        flows=flows,
        network=NetworkConfig.from_dict(raw.get("network", {})),
        modes=modes,
        duration=float(raw.get("duration", 20.0)),
        warmup=float(raw.get("warmup", 3.0)),
        repeats=int(raw.get("repeats", 1)),
        seed=int(raw.get("seed", 0)),
        quic_idle_timeout=float(raw.get("quic_idle_timeout", 2.0)),
        congestion_control=raw.get("congestion_control", "cubic"),
        sweep=point,
    )
    if exp.warmup >= exp.duration:
        raise ValueError(f"experiment {exp.name}: warmup must be shorter than duration")
    return exp


def load_experiments(path: str | Path, backend: str | None = None) -> list[Experiment]:
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    defaults = doc.get("defaults", {})
    out = []
    for raw in doc.get("experiment", []):
        raw = _deep_merge(defaults, raw)
        if backend is not None:
            raw.setdefault("network", {})["backend"] = backend
        for expanded, point in _expand_sweeps(raw):
            out.append(_build(expanded, point))
    return out


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """A free UDP port whose successor is also free (the proxy uses both)."""
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as a:
            a.bind(("127.0.0.1", 0))
            port = a.getsockname()[1]
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as b:
                    b.bind(("127.0.0.1", port + 1))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port pair")


def make_creds(directory: Path) -> tuple[str, str]:
    """Self-signed certificate for the QUIC server."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "gabriel-sim")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=7))
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = directory / "cert.pem", directory / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    return str(certfile), str(keyfile)


def _virtual_link(net: NetworkConfig):
    from .virtual import VirtualLink

    p = net.up
    translate = {"down": "down", "blackhole": "down", "delay_ms": "delay_ms",
                 "jitter_ms": "jitter_ms", "rate_mbit": "rate_mbit", "loss_pct": "loss_pct"}
    events = []
    for e in net.events:
        changes = {translate[k]: v for k, v in e.changes.items() if k in translate}
        events.append((e.at, changes))
    return VirtualLink(delay_ms=p.delay_ms, jitter_ms=p.jitter_ms, rate_mbit=p.rate_mbit,
                       loss_pct=p.loss_pct, events=events, down=p.down or p.blackhole)


def run_virtual(exp: Experiment, mode: str, repeat: int) -> list[dict]:
    from .virtual import VirtualWorld

    specs = [f.with_mode(mode) for f in exp.flows]
    for s in specs:
        s.seed = exp.seed + repeat
    world = VirtualWorld(specs, _virtual_link(exp.network), seed=exp.seed + repeat)
    return list(world.run(exp.duration, exp.warmup).values())


async def _spawn(argv: list[str], logfile: Path) -> subprocess.Popen:
    f = open(logfile, "w")
    return subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)


def _stop(proc: subprocess.Popen):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


async def run_real(exp: Experiment, mode: str, repeat: int, creds, logdir: Path) -> list[dict]:
    net = exp.network
    port = _free_port()
    network = make_network(net.backend, net.up, net.down, port)
    prefix = f"gs{uuid.uuid4().hex[:10]}"
    tag = f"{_slug(exp.name)}-{mode}-{repeat}"
    procs = []
    client = None
    await network.setup()
    try:
        py = [sys.executable, "-m", "sim.server"]
        procs.append(await _spawn([*py, "local", "--iox", prefix], logdir / f"{tag}-local.log"))
        remote = [*py, "remote", "--iox", prefix, "--quic", network.remote_bind,
                  "--cert", creds[0], "--key", creds[1],
                  "--idle-timeout", str(exp.quic_idle_timeout)]
        procs.append(await _spawn(network.remote_command(remote), logdir / f"{tag}-remote.log"))

        client = Client(f"{prefix}/local", network.remote_endpoint,
                        quic_idle_timeout=exp.quic_idle_timeout,
                        congestion_control=exp.congestion_control)
        await client.start(timeout=20)

        specs = [f.with_mode(mode) for f in exp.flows]
        for s in specs:
            s.seed = exp.seed + repeat
        await asyncio.gather(*(client.create_flow(s) for s in specs))
        loop = asyncio.get_running_loop()
        t0 = loop.time()

        async def events():
            for e in net.events:
                await asyncio.sleep(max(0.0, t0 + e.at - loop.time()))
                log.info("  t=%.1fs network %s %s", e.at, e.direction, e.changes)
                await network.apply(e)

        event_task = asyncio.create_task(events())
        await asyncio.sleep(exp.warmup)
        for s in specs:
            client.mark(s.name)
        await asyncio.sleep(max(0.0, t0 + exp.duration - loop.time()))
        event_task.cancel()
        # Restore the link so the reports can get through.
        await network.apply(NetworkEvent(0, BOTH, {"down": False, "blackhole": False}))
        return list(await asyncio.gather(*(client.stop_flow(s.name) for s in specs)))
    finally:
        if client is not None:
            await client.close()
        for p in procs:
            _stop(p)
        await network.teardown()


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

COLUMNS = [
    ("throughput", "thr/s", "{:.2f}"),
    ("lat_p50_ms", "lat50", "{:.0f}"),
    ("lat_p95_ms", "lat95", "{:.0f}"),
    ("aoi_ms", "AoI", "{:.0f}"),
    ("wasted_pct", "waste%", "{:.1f}"),
    ("wasted_transfers", "wXfer", "{:.0f}"),
    ("overwrites", "ovw", "{:.0f}"),
    ("flaps", "flaps", "{:.0f}"),
    ("idle_pct", "idle%", "{:.0f}"),
]


def flatten(exp: Experiment, mode: str, repeat: int, s: dict) -> dict:
    ms = lambda v: None if v is None else v * 1000  # noqa: E731
    engines = [v for v in s["nodes"].values() if "idle_frac" in v and v["idle_frac"] is not None]
    return {
        "experiment": exp.name,
        "flow": s["flow"],
        "mode": mode,
        "repeat": repeat,
        "backend": exp.network.backend,
        **{f"sweep.{k}": v for k, v in exp.sweep.items()},
        "throughput": s["throughput"],
        "lat_p50_ms": ms(s["latency"]["p50"]),
        "lat_p95_ms": ms(s["latency"]["p95"]),
        "lat_mean_ms": ms(s["latency"]["mean"]),
        "aoi_ms": ms(s["aoi"]),
        "runs": s["runs"],
        "wasted_runs": s["wasted_runs"],
        "wasted_pct": 100 * s["wasted_frac"],
        "unsent_runs": s["unsent_runs"],
        "rejects": s["rejects"],
        "wasted_transfers": s["wasted_transfers"],
        "overwrites": s["overwrites"],
        "lease_expiries": s["lease_expiries"],
        "flaps": s["flaps"],
        "idle_pct": 100 * sum(e["idle_frac"] for e in engines) / len(engines) if engines else None,
        "missing_hosts": ",".join(s.get("missing_hosts", [])),
        "remote_disconnects": s.get("remote_disconnects", 0),
    }


def print_table(rows: list[dict], out=sys.stdout):
    by_exp: dict[str, list[dict]] = {}
    for r in rows:
        by_exp.setdefault(r["experiment"], []).append(r)
    for exp, rs in by_exp.items():
        print(f"\n== {exp}", file=out)
        header = f"{'flow':10s} {'mode':14s} " + " ".join(f"{h:>7s}" for _, h, _ in COLUMNS)
        print(header, file=out)
        groups: dict[tuple, list[dict]] = {}
        for r in rs:
            groups.setdefault((r["flow"], r["mode"]), []).append(r)
        for (flow, mode), g in groups.items():
            cells = []
            for key, _, fmt in COLUMNS:
                vals = [r[key] for r in g if r[key] is not None]
                cells.append(f"{fmt.format(sum(vals) / len(vals)):>7s}" if vals else f"{'-':>7s}")
            print(f"{flow:10s} {mode:14s} " + " ".join(cells), file=out)


async def run_all(experiments: list[Experiment], out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    logdir = out_dir / "logs"
    logdir.mkdir(exist_ok=True)
    timelines = out_dir / "timelines"
    timelines.mkdir(exist_ok=True)
    needs_root = any(e.network.backend == "netns" for e in experiments)
    if needs_root and os.geteuid() != 0:
        raise SystemExit(
            "the netns backend needs root (sudo .venv/bin/sim run ...); "
            "or use --backend proxy / direct / virtual"
        )
    creds = make_creds(Path(tempfile.mkdtemp(prefix="gabsim-creds-")))
    rows = []
    jsonl = open(out_dir / "results.jsonl", "a")
    for exp in experiments:
        for repeat in range(exp.repeats):
            for mode in exp.modes:
                log.info("%s  mode=%s  repeat=%d  (%s)", exp.name, mode, repeat, exp.network.backend)
                started = time.time()
                try:
                    if exp.network.backend == "virtual":
                        summaries = run_virtual(exp, mode, repeat)
                    else:
                        summaries = await run_real(exp, mode, repeat, creds, logdir)
                except Exception as e:
                    log.exception("run failed: %s", e)
                    continue
                for s in summaries:
                    row = flatten(exp, mode, repeat, s)
                    rows.append(row)
                    timeline = s.pop("timeline", {})
                    with open(timelines / f"{_slug(exp.name)}-{mode}-{repeat}-{s['flow']}.csv", "w") as f:
                        f.write("leaf,t,latency,partial\n")
                        for leaf, points in timeline.items():
                            for t, lat, partial in points:
                                f.write(f"{leaf},{t:.4f},{lat:.4f},{partial}\n")
                    jsonl.write(json.dumps({"row": row, "summary": s}) + "\n")
                    jsonl.flush()
                    log.info("  %s: thr=%.2f/s lat50=%s aoi=%s waste=%.1f%%  (%.0fs)",
                             s["flow"], row["throughput"] or 0,
                             f"{row['lat_p50_ms']:.0f}ms" if row["lat_p50_ms"] else "-",
                             f"{row['aoi_ms']:.0f}ms" if row["aoi_ms"] else "-",
                             row["wasted_pct"], time.time() - started)
    jsonl.close()
    if rows:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(out_dir / "summary.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sim", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run experiments")
    run.add_argument("config")
    run.add_argument("--out", default=None, help="results directory")
    run.add_argument("--only", nargs="*", help="experiment names (prefix match)")
    run.add_argument("--backend", choices=BACKENDS, help="override every experiment's backend")
    run.add_argument("--modes", nargs="*", choices=MODES, help="override modes")
    run.add_argument("--log", default="INFO")
    check = sub.add_parser("check", help="validate a config and list the runs")
    check.add_argument("config")
    check.add_argument("--backend", choices=BACKENDS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(args, "log", "INFO"), format="%(asctime)s %(message)s")

    experiments = load_experiments(args.config, backend=args.backend)
    if args.cmd == "check":
        for e in experiments:
            flows = ", ".join(f"{f.name}({len(f.nodes)} nodes)" for f in e.flows)
            print(f"{e.name}: backend={e.network.backend} modes={e.modes} repeats={e.repeats} "
                  f"duration={e.duration}s flows={flows} events={len(e.network.events)}")
        return
    if args.only:
        experiments = [e for e in experiments if any(e.name.startswith(o) for o in args.only)]
    if args.modes:
        for e in experiments:
            e.modes = args.modes
    out = Path(args.out or f"results/{datetime.datetime.now():%Y%m%d-%H%M%S}")
    rows = asyncio.run(run_all(experiments, out))
    print_table(rows)
    print(f"\nresults in {out}/ (summary.csv, results.jsonl, timelines/, logs/)")


if __name__ == "__main__":
    main()
