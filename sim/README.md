# sim: schedule-paced compute graph

An implementation of the v1 pacing protocol (`src/sim/protocol.txt`;
design context in `CLAUDE.md`), plus the baselines it is compared against
and an experiment runner with network emulation.

## Architecture

```
        client host                                     remote host
 ┌──────────────────────────────────────┐          ┌──────────────────────┐
 │ local server        client           │   QUIC   │ remote server        │
 │ FlowManager ◄─iox─► FlowRouter ◄─────┼──────────┼─► FlowManager        │
 │  │ local engines    Sources, sink    │ (netem / │   │ remote engines   │
 │  └─iox─ engine      edge agent       │  proxy)  │   └─iox─ engine      │
 │         processes   (RTT, T)         │          │      processes       │
 └──────────────────────────────────────┘          │   edge agent (RTT,T) │
                                                   └──────────────────────┘
```

- **Client** (`client.py`) owns the Sources and the result sink. Creating a
  flow sends the same `FlowSpec` to both servers. Its `FlowRouter` forwards
  every message that changes hosts (local server ↔ remote server, servers ↔
  Sources/sink) without decoding data payloads.
- **Servers** (`server.py`) each create a `FlowManager` per flow for the
  nodes placed on their host. Edges inside one server are delivered
  in-process. Engines are dummy worker processes (`engine.py`) reached over
  iceoryx2. The server sees them only as black boxes and measures R.
- **Transport** (`transport.py`): all local connections use iceoryx2, the
  remote one uses QUIC. Every graph edge has its own single-slot data channel
  (`flow|parent|child`), so flows and edges never block or overwrite each
  other. Each frame goes out as one QUIC stream, written only after the
  previous frame on that channel has left. Control is a separate reliable
  FIFO stream.
- **Nodes** (`nodes.py`): one event-driven state machine per node, written
  against a `NodeIO` interface (clock, timers, send, run engine). The same
  code runs under asyncio and under the virtual clock (`virtual.py`).
- **Edge agents** (`edge.py`): the router and the remote server each apply
  the network corrections once. The receiving side folds in T (measured per
  frame, first byte to last byte). The sending side subtracts RTT (median of
  ping round trips).
- **Graphs** (`flow.py`): NetworkX DiGraph built from the node list;
  validated (DAG, placement, names) before it is sent to the servers.

## Modes compared

| mode       | behavior |
|------------|----------|
| `schedule` | protocol v1: demands with interval, phase and lease. The Source sends on a timer; interior nodes send when outputs are ready. |
| `push`     | the Source publishes every frame; every output goes to every child; newest-wins slots |
| `pull`     | one-ahead pull: a leaf requests at each run start; an interior node requests when a child is waiting |
| `token`    | CONWIP: push, but each Source holds N tokens, one per frame in flight and shared by all of its edges. A token returns on a result, a filter rejection, an overwrite in an engine's input slot or in a QUIC sender slot before sending, or after a timeout. |
| `token-adaptive` | `token`, but each Source adjusts N at most once per token round trip: +1 when a frame arrives with every token out and no overwrite in the last round trip, −1 when a token returns from an overwrite or is reclaimed after a timeout (`tokens` is the starting count, `tokens_max` the cap). |
| `credit`   | hop-by-hop credits. Each child grants each parent a window of sends (in flight plus waiting inside the child) and hands a credit back once an input has left it: sent on, rejected or overwritten. It adapts each window itself: +1 when it sat idle while the edge was out of credits, −1 on an overwrite or a frame that never arrived. A node doesn't start a run while every child already holds an output. Credits and send counts are cumulative, so lost messages are harmless. (`credit_init`, `credit_timeout_ms`, `tokens_max`) |
| `schedule-hop` | `credit`, plus timing. Each credit is held until its input is needed: the later of when this node will be free and when its fastest child will next be ready, minus this node's median run time and a per-edge lead. The lead starts as the measured grant-to-arrival time and is corrected by `lead_gain` × each arrival's error (late arrivals only count when the parent was waiting for that credit). A parent predicts when a child will next be ready from the rhythm of its credits. Everything is per hop: no phase is passed on, and there are no RTT pings, T estimates or send grids. |
| `segment`  | pull on local edges, fixed tokens on remote edges. **Local edges** (same host): a child asks its parent for input only when it's idle and could pass a result on. The parent answers at once with the newest thing it has: a Source its newest frame, an engine the output it keeps ready. An engine keeps at most one output ready per local child, so it only runs while some child could take the result. **Remote edges** (crossing the network; their sender is implicitly a fan-out): each is a branch with a fixed `segment_remote_tokens` (6). An arrival goes out once, right away, to every remote child with a free token (one transfer, delivered on the far host to each). A branch's token returns when its far-side chain ends: a leaf result, a filter rejection, or a fan-out forwarding the frame. A skip parks the token until `token_timeout_ms` while another of the branch's frames is out, and parked tokens come back one at a time. A node with remote children watches the link (QUIC state plus 500 ms without a ping reply). During an outage it holds those tokens, and releases them when the link returns. `segment_branch_tokens = false` selects the older version with per-group tokens. |

## Running experiments

```bash
uv sync
uv run sim check experiments.toml                  # validate, list runs
uv run sim run experiments.toml --backend virtual  # simulated time, seconds
uv run sim run experiments.toml --backend proxy    # real stack, userspace link emulator
sudo .venv/bin/sim run experiments.toml            # real stack, netns + netem (default backend)
uv run sim run experiments.toml --only chain --modes schedule push
```

Each run writes `results/<timestamp>/`:
- `summary.csv`: one row per (experiment, flow, mode, repeat).
- `results.jsonl`: full summaries, with per-node metrics.
- `timelines/`: one row per result: time, latency, whether the run was partial.
- `logs/`: server logs, including the remote edge agent's RTT and T estimates.

A table comparing the modes is printed at the end.

Backends:
- `netns`: the remote server runs in a network namespace behind a veth pair,
  with netem (delay, jitter, loss, rate, queue limit) on both ends, managed
  with pyroute2. Needs root.
- `proxy`: a userspace UDP relay with the same parameters. No root needed,
  but timing is coarser.
- `direct`: loopback, no shaping.
- `virtual`: no processes; the node state machines run on a simulated clock
  with a simple link model. Use it for fast sweeps.

### Config format

See the comments at the top of `experiments.toml`. Experiments run
sequentially. Each one has flows (nodes with `kind`, `host`, `parents`,
service-time distribution, output size, `pass_rate`, `grace_ms`), a network
(per-direction overrides under `network.up` / `network.down`), timed
`network.events` (`down`, `blackhole`, `rate_mbit`, `delay_ms`, …),
protocol `params`, `modes`, `repeats`, and `sweep` (the cartesian product of
dotted keys, e.g. `"network.delay_ms" = [10, 40]`). A `[defaults]` table is
merged into every experiment.

### Metrics

Throughput; latency (result arrival − oldest input sample's timestamp);
time-averaged age of information; wasted runs (outputs no child consumed),
with unsent runs for the protocol's narrower definition; wasted transfers
(network frames overwritten before use); overwrites; per-node idle fraction
and slot wait; lease expiries and flaps. Node counters are cut to each
host's own measurement window (`mark` → `stop`), so no clock sync is needed.

## Where the implementation differs from protocol.txt

Each change came from a failure seen in testing (details are in the code
comments):

1. **Interior nodes are output-driven with a per-edge interval, and no
   phase hold.** protocol.txt also delays an interior edge's next send until
   a phase point (`calc_next`), but CLAUDE.md says the Source is "the only
   place Phase sets a send time". Holding outputs until a phase point made
   them stale and caused overwrites. Here an output goes out on each leased
   edge as soon as `I − ε` has passed since that edge's last send, and is held
   in the edge's slot until then. The per-edge interval is still needed: at a
   fan-out the Source follows the fastest child, so without it a slow child
   gets every output (tried: about 300 wasted transfers per run in
   fanout-join).
2. **The Source keeps its schedule on renewals that carry the same Seq.**
   Idle heartbeats (Phase 0) re-anchored the grid. When the wrapped phase is
   longer than the heartbeat period W, each renewal pushed the next send out
   again before it fired, and the edge starved. This happened on the real
   stack with I ≈ 110 ms and W = 50 ms.
3. **A re-anchored Source send is held until `last_send + I − ε`.** Demands
   with a new Seq can arrive faster than I. Re-anchoring on each one then made
   the Source send at the demand rate rather than at I.
4. **"Never send the same output twice" compares producer run ids, not
   sample timestamps.** A partial join run can carry an older newest
   timestamp than the previous run while still being new output.
5. **Best/Worst use the median run time (p50), not p80**
   (`params.pace_pct`, default 50; set 80 for protocol.txt's behavior).
   Committing to p80 caps throughput at 1/p80. On top of that, per-run
   re-anchoring makes Source gaps one-sidedly longer than I. In virtual time,
   with detect at mean 60 ms and cv 0.3, this took schedule mode from
   10.9 to 15.7 results/s (token mode: 16.5). The cost is an occasional
   overwritten input when a run exceeds the median. Leases still use p80.
6. **T uses the median time per byte (p50), not p90**
   (`params.transmit_pct`, default 50; set 90 for protocol.txt's behavior).
   On the real stack (proxy, 50 KB frames, 50 Mbit/s, 46 ms RTT), schedule
   mode went from 9.5 to 12.4 results/s. Median latency rose from 205 to
   248 ms, because the link stays busier.
7. **The Source sends the next frame if a send slot finds none newer**,
   instead of skipping the slot (CLAUDE.md principle 8).
8. **Seq is kept per parent edge and updated on arrival**, as CLAUDE.md
   describes. protocol.txt kept a single `n.seq`, which a partial join run
   could lower.

`src/sim/protocol.txt` has been updated to match, with a changelog at the
end.

Not implemented: cancelling an in-flight frame with RESET_STREAM (optional in
the spec).

## Observations so far

- With service-time jitter and pacing at p80, `schedule` ran at about
  1/p80(R) of the bottleneck, minus losses from re-anchoring. Hence the
  p50 default above.
- On a real QUIC link, `Worst = max(R, T)` is often set by T rather than R.
  T uses p90 of time per byte as the spec says, and frames are paced by
  aioquic, so a 50 KB frame at 50 Mbit/s and 46 ms RTT measures 70–120 ms.
  Hence item 6 above.
- T is measured per frame. While frames are rare it includes slow-start /
  small-cwnd effects, so T starts high (~100 ms for 20 KB at 40 ms RTT). It
  converges once frames flow (~20 ms, mostly aioquic pacing).

## Tests

```bash
uv run pytest -q
```

`test_protocol.py` covers the protocol in virtual time. `test_transport.py`
covers both transports. `test_system.py` covers configs and two end-to-end
runs with real processes over loopback. The netns backend can't be
exercised without root; its netem parameters are checked against pyroute2's
encoder.
