"""Protocol behavior in virtual time (no processes, deterministic)."""

import pytest

from sim import messages as m
from sim.flow import MODES, FlowSpec, NodeSpec, Params, Service
from sim.metrics import age_of_information
from sim.nodes import ScheduleEngine, next_on_grid
from sim.virtual import VirtualLink, VirtualWorld


def chain(mode="schedule", rb=50, rc=30, cv=0.2, **params):
    nodes = [
        NodeSpec("cam", kind="source", period_ms=10, frame_bytes=20000),
        NodeSpec("a", host="local", parents=["cam"], service=Service("lognormal", 20, cv)),
        NodeSpec("b", host="remote", parents=["a"], service=Service("lognormal", rb, cv),
                 output_bytes=5000),
        NodeSpec("c", host="local", parents=["b"], service=Service("lognormal", rc, cv)),
    ]
    return FlowSpec("f", nodes, mode=mode, params=Params(**params))


def run(spec, link=None, duration=20.0, warmup=2.0):
    return VirtualWorld([spec], link or VirtualLink(delay_ms=20, rate_mbit=20)).run(duration, warmup)[spec.name]


def test_next_on_grid():
    assert next_on_grid(5.0, 2.0, 3.0) == 5.0
    assert next_on_grid(1.0, 2.0, 4.0) == 5.0
    assert next_on_grid(1.0, 2.0, 5.0) == 5.0


def test_age_of_information():
    # Results every 1s, each 0.5s old on arrival: age ramps 0.5 -> 1.5.
    results = [(t + 0.5, float(t)) for t in range(10)]
    assert age_of_information(results, 2.5, 8.5) == pytest.approx(1.0)
    assert age_of_information([], 0, 1) is None


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_delivers(mode):
    r = run(chain(mode))
    assert r["throughput"] > 5
    assert r["latency"]["p50"] < 0.3


def test_schedule_paces_to_bottleneck_without_waste():
    r = run(chain())
    # b (~50 ms median) sets the pace: ~19/s; push would run `a` at 50/s.
    assert 16 < r["throughput"] < 21
    assert r["wasted_frac"] < 0.02
    # Pacing at the median: runs longer than p50 occasionally let an input
    # be overwritten, but rarely.
    assert r["wasted_transfers"] < 0.02 * r["runs"]
    assert r["nodes"]["a"]["runs"] < 1.3 * r["nodes"]["c"]["runs"]


def test_push_wastes_work():
    sched, push = run(chain()), run(chain("push"))
    assert push["wasted_runs"] > 10 * max(1, sched["wasted_runs"])
    assert push["wasted_transfers"] > 100


def test_pace_percentile_trades_throughput_for_overwrites():
    p50, p80 = run(chain(pace_pct=50)), run(chain(pace_pct=80))
    assert p50["throughput"] > p80["throughput"]
    assert p80["wasted_transfers"] <= p50["wasted_transfers"]


def test_constant_service_reaches_capacity():
    r = run(chain(cv=0.0))
    assert r["throughput"] == pytest.approx(20, rel=0.05)


def test_outage_expires_leases_and_recovers():
    link = VirtualLink(delay_ms=20, rate_mbit=20, events=[(6.0, {"down": True}), (8.0, {"down": False})])
    spec = chain()
    world = VirtualWorld([spec], link)
    r = world.run(20.0, 2.0)["f"]
    assert r["lease_expiries"] > 0
    sink = world.sinks["f"].results["c"]
    t0 = world.clock.now() - 20.0
    after = [x for x in sink if x[0] > t0 + 10]
    assert len(after) > 0.6 * 10 * 17  # back near full rate after the outage
    gap = [x for x in sink if t0 + 6.5 < x[0] < t0 + 8.0]
    assert not gap


def test_stale_demand_discarded():
    spec = chain()
    world = VirtualWorld([spec])
    a = world.nodes[("f", "a")]
    assert isinstance(a, ScheduleEngine)
    a.on_control(m.demand("f", "b", "a", 0.05, 0.05, 0.0, 10.0, 1.0))
    assert a.dc["b"].last_seq == 10.0
    a.on_control(m.demand("f", "b", "a", 0.05, 0.05, 0.0, 5.0, 1.0))
    assert a.dc["b"].last_seq == 10.0  # older Seq ignored
    a.on_control(m.demand("f", "b", "a", 0.05, 0.05, 0.0, 0, 1.0))
    assert a.dc["b"].last_seq == 0  # Seq 0 = restart, always accepted


def test_fanout_paces_each_child():
    nodes = [
        NodeSpec("cam", kind="source", period_ms=10),
        NodeSpec("a", host="local", parents=["cam"], service=Service("const", 5)),
        NodeSpec("fast", host="local", parents=["a"], service=Service("const", 20)),
        NodeSpec("slow", host="local", parents=["a"], service=Service("const", 80)),
    ]
    spec = FlowSpec("f", nodes)
    world = VirtualWorld([spec])
    r = world.run(10.0, 2.0)["f"]
    # Fan-out: the Source follows the fastest child, so each child edge is
    # paced separately; the slow child never gets the fast child's extra
    # outputs.
    assert r["leaves"]["fast"]["throughput"] == pytest.approx(50, rel=0.15)
    assert r["leaves"]["slow"]["throughput"] == pytest.approx(12.5, rel=0.15)
    assert r["nodes"]["slow"]["overwrites"] <= 2  # eps tolerance at exact service times


def test_join_with_grace_runs_partial():
    nodes = [
        NodeSpec("cam", kind="source", period_ms=10),
        NodeSpec("fast", host="local", parents=["cam"], service=Service("const", 20)),
        NodeSpec("slow", host="remote", parents=["cam"], service=Service("const", 60)),
        NodeSpec("fuse", host="local", parents=["fast", "slow"], grace_ms=5,
                 service=Service("const", 2)),
    ]
    r = run(FlowSpec("f", nodes))
    assert r["nodes"]["fuse"]["partial"] > 0
    assert r["throughput"] > 20  # runs near the fast branch's pace


def test_filter_and_tokens():
    nodes = [
        NodeSpec("cam", kind="source", period_ms=10),
        NodeSpec("f1", host="local", parents=["cam"], service=Service("const", 10), pass_rate=0.5),
        NodeSpec("g", host="remote", parents=["f1"], service=Service("const", 10)),
    ]
    spec = FlowSpec("f", nodes, mode="token", params=Params(tokens=2))
    world = VirtualWorld([spec], VirtualLink(delay_ms=10))
    r = world.run(10.0, 2.0)["f"]
    assert r["rejects"] > 0
    assert r["throughput"] > 5
    # Rejections return their tokens, so nothing had to be reclaimed by timeout.
    assert r["nodes"]["cam"]["tokens_reclaimed"] == 0


def test_transmit_percentile_parameter():
    from sim.edge import EdgeAgent

    agent = EdgeAgent(side_is_remote=True)
    chan = "f|a|b"
    for ms in [10] * 8 + [50, 90]:  # 10 frames of 10 kB
        agent.on_frame(chan, 10_000, ms / 1000)
    # Demand from b (remote child) to a (local parent) leaves the remote side.
    spec = chain()
    msg = m.demand("f", "b", "a", 0.02, 0.02, 0.0, 1.0, 0.15)
    spec.params.transmit_pct = 50
    assert agent.outgoing(spec, msg)["worst"] == pytest.approx(0.020)  # max(R, T=10 ms)
    spec.params.transmit_pct = 90
    assert agent.outgoing(spec, msg)["worst"] == pytest.approx(0.050)


def test_token_adaptive_tracks_round_trip():
    def window(delay_ms):
        spec = chain("token-adaptive", tokens=2)
        world = VirtualWorld([spec], VirtualLink(delay_ms=delay_ms, rate_mbit=50))
        r = world.run(30.0, 5.0)["f"]
        return r["nodes"]["cam"]["token_window"], r["throughput"]

    short, thr_short = window(10)
    long, thr_long = window(150)
    assert long > short + 2  # more tokens in flight on the longer loop
    assert thr_long > 0.8 * thr_short  # and throughput holds up


def test_credit_paces_fanout_and_long_hops():
    nodes = [
        NodeSpec("cam", kind="source", period_ms=20, frame_bytes=30000),
        NodeSpec("fast", host="local", parents=["cam"], service=Service("lognormal", 20, 0.2)),
        NodeSpec("slow", host="remote", parents=["cam"], service=Service("lognormal", 80, 0.3)),
    ]
    r = VirtualWorld([FlowSpec("f", nodes, mode="credit")], VirtualLink(delay_ms=20, rate_mbit=50)).run(20.0, 3.0)["f"]
    # Per-edge credits: each branch at its own pace, almost nothing wasted.
    assert r["leaves"]["fast"]["throughput"] > 40
    assert r["leaves"]["slow"]["throughput"] > 10
    assert r["wasted_transfers"] < 20

    # The window on the long hop grows to cover it.
    world = VirtualWorld([chain("credit")], VirtualLink(delay_ms=150, rate_mbit=50))
    r = world.run(30.0, 5.0)["f"]
    assert r["throughput"] > 15
    assert world.nodes[("f", "c")].window["b"] > 3


def test_schedule_hop_times_credits():
    nodes = [
        NodeSpec("cam", kind="source", period_ms=20, frame_bytes=30000),
        NodeSpec("fast", host="local", parents=["cam"], service=Service("lognormal", 20, 0.2)),
        NodeSpec("slow", host="remote", parents=["cam"], service=Service("lognormal", 80, 0.3)),
    ]
    link = VirtualLink(delay_ms=20, rate_mbit=50)
    hop = VirtualWorld([FlowSpec("f", nodes, mode="schedule-hop")], link).run(20.0, 3.0)["f"]
    credit = VirtualWorld([FlowSpec("f", nodes, mode="credit")], link).run(20.0, 3.0)["f"]
    assert hop["leaves"]["fast"]["throughput"] > 40 and hop["leaves"]["slow"]["throughput"] > 10
    assert hop["wasted_transfers"] <= credit["wasted_transfers"]
    # Timed credits: inputs no longer queue in front of the slow branch.
    assert hop["leaves"]["slow"]["latency"]["p50"] < credit["leaves"]["slow"]["latency"]["p50"]

    world = VirtualWorld([chain("schedule-hop")], VirtualLink(delay_ms=150, rate_mbit=50))
    r = world.run(30.0, 5.0)["f"]
    assert r["throughput"] > 15
    assert all(lead is None or lead < world.nodes[("f", "c")].LEAD_MAX for lead in world.nodes[("f", "c")].lead.values())


def test_segment_group_mode_single_copy():
    nodes = [
        NodeSpec("cam", kind="source", period_ms=20, frame_bytes=30000),
        NodeSpec("pre", host="local", parents=["cam"], service=Service("lognormal", 5, 0.2)),
        NodeSpec("fast", host="local", parents=["pre"], service=Service("lognormal", 20, 0.2)),
        NodeSpec("r1", host="remote", parents=["pre"], service=Service("lognormal", 40, 0.3)),
        NodeSpec("r2", host="remote", parents=["pre"], service=Service("lognormal", 120, 0.3)),
    ]
    spec = FlowSpec("f", nodes, mode="segment", params=Params(segment_branch_tokens=False))
    assert spec.groups("pre") == {"local": ["fast"], "remote": ["r1", "r2"]}
    assert spec.groups("fast") == {} and spec.groups("cam") == {"local": ["pre"]}
    assert spec.segment_crosses("pre", "remote") and not spec.segment_crosses("cam", "local")

    world = VirtualWorld([spec], VirtualLink(delay_ms=20, rate_mbit=30))
    r = world.run(20.0, 3.0)["f"]
    # One transfer per frame for both remote consumers; the slow one skips.
    assert set(world.nodes[("f", "pre")].metrics.sends) == {"fast", "r1+r2"}
    assert r["leaves"]["r2"]["throughput"] > 6 and r["leaves"]["r1"]["throughput"] > 18
    assert r["wasted_transfers"] < 100

    # Generous remote count covers a long hop; parking keeps waste down.
    r = VirtualWorld([chain("segment", segment_branch_tokens=False)],
                     VirtualLink(delay_ms=150, rate_mbit=50)).run(30.0, 5.0)["f"]
    assert r["throughput"] > 14 and r["wasted_frac"] < 0.05


def test_segment_join_without_grace_parks_instead_of_overrunning():
    nodes = [
        NodeSpec("cam", kind="source", period_ms=33.3, frame_bytes=30000),
        NodeSpec("mic", kind="source", period_ms=20, frame_bytes=4000),
        NodeSpec("det", host="remote", parents=["cam"], service=Service("lognormal", 50, 0.3)),
        NodeSpec("asr", host="local", parents=["mic"], service=Service("lognormal", 15, 0.2)),
        NodeSpec("fuse", host="local", parents=["det", "asr"], grace_ms="none",
                 service=Service("const", 3)),
    ]
    spec = FlowSpec("f", nodes, mode="segment")
    assert spec.node("fuse").grace_ms is None
    r = VirtualWorld([spec], VirtualLink(delay_ms=20, rate_mbit=50)).run(30.0, 5.0)["f"]
    assert r["throughput"] > 15
    assert r["wasted_frac"] < 0.05  # the fast branch doesn't overrun the join


def _fan(mode, remote_children=1, **params):
    nodes = [
        NodeSpec("cam", kind="source", period_ms=20, frame_bytes=30000),
        NodeSpec("pre", host="local", parents=["cam"], service=Service("lognormal", 5, 0.2)),
        NodeSpec("fast", host="local", parents=["pre"], service=Service("lognormal", 20, 0.2)),
        NodeSpec("r1", host="remote", parents=["pre"], service=Service("lognormal", 40, 0.3)),
    ]
    if remote_children > 1:
        nodes.append(NodeSpec("r2", host="remote", parents=["pre"], service=Service("lognormal", 120, 0.3)))
    return FlowSpec("f", nodes, mode=mode, params=Params(**params))


def test_segment_local_pull_has_no_queue():
    # A local consumer pulls the newest input when idle: full throughput,
    # latency close to its own run time, nothing wasted.
    world = VirtualWorld([_fan("segment")], VirtualLink(delay_ms=20, rate_mbit=50))
    r = world.run(20.0, 3.0)["f"]
    fast = r["leaves"]["fast"]
    assert fast["throughput"] > 45
    assert fast["latency"]["p50"] < 0.04
    assert r["wasted_frac"] < 0.02


def test_segment_remote_branches_send_once_and_hold_tokens():
    world = VirtualWorld([_fan("segment", remote_children=2)], VirtualLink(delay_ms=20, rate_mbit=30))
    r = world.run(20.0, 3.0)["f"]
    pre = world.nodes[("f", "pre")]
    # One copy for both remote consumers while both have tokens.
    assert "r1+r2" in pre.metrics.sends
    assert all(len(b.out) <= b.n for b in pre.branches.values())
    assert r["leaves"]["r2"]["throughput"] > 6 and r["leaves"]["r1"]["throughput"] > 18
    assert r["wasted_frac"] < 0.02


def test_segment_link_outage_holds_and_releases():
    link = VirtualLink(delay_ms=20, rate_mbit=50, events=[(6.0, {"down": True}), (8.0, {"down": False})])
    world = VirtualWorld([_fan("segment")], link)
    pre = world.nodes[("f", "pre")]
    seen = {}
    world.clock.call_at(world.clock.now() + 7.5, lambda: seen.update(down=(pre.link_up, dict(pre.branches["r1"].out))))
    r = world.run(20.0, 3.0)["f"]
    link_up, out = seen["down"]
    assert not link_up and out  # tokens held, not timed out, during the outage
    assert pre.link_up
    after = [x for x in world.sinks["f"].results["r1"] if x[0] > world.clock.now() - 10]
    assert len(after) > 0.7 * 10 * 20  # back near full rate after the outage
