"""Configs and the full system (servers as processes, iceoryx2 + QUIC)."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from sim.flow import FlowSpec, NodeSpec, Service
from sim.simulator import load_experiments, make_creds, run_real

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads():
    exps = load_experiments(ROOT / "experiments.toml")
    names = [e.name for e in exps]
    assert "chain" in names and "fanout-join[delay_ms=10]" in names
    sweep = [e for e in exps if e.name.startswith("fanout-join")]
    assert [e.network.up.delay_ms for e in sweep] == [10, 40]
    outage = next(e for e in exps if e.name == "chain-outage")
    assert [ev.changes for ev in outage.network.events][:2] == [{"down": True}, {"down": False}]


def test_invalid_specs_rejected():
    with pytest.raises(ValueError):
        FlowSpec("f", [NodeSpec("e", parents=["nope"])]).validate()
    with pytest.raises(ValueError):
        FlowSpec("f", [NodeSpec("s", kind="source"), NodeSpec("e", host="client", parents=["s"])]).validate()
    with pytest.raises(ValueError):
        FlowSpec("f", [NodeSpec("s", kind="source"), NodeSpec("a|b", parents=["s"])]).validate()


@pytest.mark.parametrize("mode", ["schedule", "push"])
def test_end_to_end_direct(mode):
    exp = load_experiments(ROOT / "experiments.toml", backend="direct")[0]
    exp.duration, exp.warmup = 6.0, 2.0
    exp.flows = [FlowSpec("e2e", [
        NodeSpec("cam", kind="source", period_ms=20, frame_bytes=20000),
        NodeSpec("a", host="local", parents=["cam"], service=Service("const", 10)),
        NodeSpec("b", host="remote", parents=["a"], service=Service("const", 40)),
        NodeSpec("c", host="local", parents=["b"], service=Service("const", 10)),
    ])]

    async def body():
        creds = make_creds(Path(tempfile.mkdtemp()))
        return await run_real(exp, mode, 0, creds, Path(tempfile.mkdtemp()))

    (summary,) = asyncio.run(asyncio.wait_for(body(), 60))
    assert summary["missing_hosts"] == []
    assert summary["throughput"] > 12  # b sets the pace at 25/s
    assert summary["latency"]["p50"] < 0.25


def test_netem_parameters_accepted_by_pyroute2():
    from pyroute2.netlink.rtnl.tcmsg import sched_netem

    from sim.network import LinkParams

    opts = sched_netem.get_parameters(
        LinkParams(delay_ms=20, jitter_ms=2, loss_pct=1, rate_mbit=12.5, limit_pkts=30).netem_kwargs()
    )
    assert opts["delay"] > 0 and opts["limit"] == 30
    assert any(a[0] == "TCA_NETEM_RATE" for a in opts["attrs"])
    blackhole = sched_netem.get_parameters(LinkParams(blackhole=True).netem_kwargs())
    assert blackhole["loss"] == 2**32 - 1


def test_template_loads_and_down_flag_vs_table(tmp_path):
    from sim.simulator import NetworkConfig

    assert load_experiments(ROOT / "template.toml")
    net = NetworkConfig.from_dict({"down": False, "delay_ms": 5, "up": {"rate_mbit": 10}})
    assert net.up.rate_mbit == 10 and net.down.rate_mbit is None and not net.down.down
    net = NetworkConfig.from_dict({"down": {"delay_ms": 30}})
    assert net.down.delay_ms == 30 and net.up.delay_ms == 0
