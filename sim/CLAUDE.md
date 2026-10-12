# Schedule-paced compute graph

Design context for a DAG scheduler. Read this before changing the scheduler.
The **v1 protocol** below is the spec to implement; "Deferred" lists ideas
deliberately left out of v1.

## Problem

- A DAG compute graph. Roots are **Sources**, which gain data at a fixed rate
  and choose when to publish. Every other node is an **Engine**: it runs a
  fixed computation on each input and passes the result to its children.
  Engines with no children (leaves) return results to the Source.
- **Local Engines** share a host with the Source. **Remote Engines** share a
  host with each other on a different host. The link to them is unstable: it
  can go down or lose bandwidth.
- We control the Source and every edge. We do **not** control engines: their
  speed is unknown and they don't know it either.
- Some engines are **filters**: they may reject an input and produce nothing.
- **All Sources share one clock**, so sample timestamps are comparable
  across Sources (engines and remote hosts need no clock sync).
- Goals: maximize throughput, minimize latency, minimize wasted work (engine
  runs whose output nobody consumes). **No queuing** (buffer bloat); every
  engine should get the freshest data possible.

## Principles

1. **Every edge is a single slot.** A newer value overwrites an older one.
   The slot is emptied when the engine takes the value and is full again only
   when new data arrives (read-once; values are never reused).
2. **Demand flows upstream, data flows down.** Nothing is sent without a
   downstream demand for it.
3. **Demand is a schedule: interval I, phase, lease.** Interval sets
   throughput and waste (too fast → overwrites, too slow → idle). Phase sets
   freshness (right interval, wrong phase → inputs wait up to I in the slot).
   Lease handles failure.
4. **Pace is set by the slowest stage on the path; phase is anchored at the
   leaf.** Each hop clamps the interval by its own run time and moves the
   phase earlier by its own run time (and link time at network hops).
5. **Every node is a mini-Source downstream.** It keeps an interval and a
   lease per outgoing edge and sends its newest output on that edge at most
   once per interval (the Source on a phase-timed schedule; interior nodes as
   outputs become ready). Between sends, outputs simply overwrite each other
   (no cost). A slow child never receives a fast child's
   extra outputs. Upstream, each node is an ordinary consumer sending one
   combined demand.
6. **Waste:** a run is wasted only if no edge sent its output. A **wasted
   transfer** is a frame that crossed a network link and was overwritten at
   the receiver before use.
7. **Network edges** (one edge agent per remote host): never let the OS queue
   data (write only when the previous payload has left; sender-side single
   slot; an output already transmitting is never replaced); control messages
   on a separate stream from data; demand phases are durations, not
   timestamps (no clock sync); nothing is replayed after an outage. Cancel a
   transfer mid-flight (RESET_STREAM) only if the newer output can still
   arrive before the remote engine frees, since sent bytes are wasted.
8. **Sources with a frame period P:** the Source clamps `I ≥ P`, sends right
   after a frame arrives, and when I isn't a whole number of frames chooses
   frames by running error (`credit += P` per frame; send when
   `credit ≥ I`, then `credit −= I`). Measure P from arrival timestamps.

### Corrections made during design (don't reintroduce these)

- Safety margin must **not** be subtracted at every hop (margins stack up).
- On network edges, a **schedule** beats per-item requests: control-path
  jitter stops mattering and data age is set by the one-way trip.
- **End-to-end tokens** (window / CONWIP / flow limiter) were rejected as the
  pacing mechanism: no phase, coarse N, the loop includes the return trip,
  every mid-graph drop must return a token, fan-out/joins don't map to one
  pool, outage-lost tokens need timeouts (i.e. leases).
- Interval alone is not enough: dropping phase gives correct throughput but
  up to a full interval of extra staleness.
- Don't push every output to every child: an early arrival starts the child
  ahead of its pace (stale intended input, oversupplied grandchildren).

## Protocol v1 (per node)

```
Each node keeps:
  R   estimate of its engine's run time (seeded, updated every run). For
      the leaf's predicted free time use a low percentile, so inputs tend
      to arrive slightly early (costs a little freshness) rather than late
      (costs throughput).
  Measured per node as metrics (not fed back in v1):
      slot_wait = time the input sat in the slot before the run (early)
      idle      = time the engine waited for input (late; a join's grace
                  wait is not idle)
Network edges (no dependence on QUIC stack stats or congestion controller):
  RTT: the SENDING side (of data) pings over the control stream; the far
       side echoes. Use the median of recent samples (pings share packet
       scheduling with data frames, so min underestimates under load and
       max overreacts to spikes).
  T:   measured per frame at the RECEIVING side as last-byte − first-byte
       arrival (a duration, no clock sync). Captures whatever the stack did:
       any congestion controller, cwnd smaller than a frame, pacing,
       retransmits, slow restart after idle. Send each frame in one write /
       one QUIC stream so the gap measures the link, not the sender.
       T = frame size × p90(recent time per byte) (frame sizes vary).
       Don't use bytes ÷ elapsed time: frames are paced, so the link idles
       between them and that measures the send rate, not capacity.
  Receiving side, before sending its demand up: Worst = max(Worst, T);
       Phase −= T.
  Sending side, on receiving a demand: Phase −= RTT.

Demand (child → parent, per edge): Best, Worst, Phase, Lease, Seq
  Best  = best-case arrival interval the consumer could use
  Worst = path capacity (slowest of run times and link transmit times)
  Phase = time until the Source should send (duration, at send)
  Seq   = newest sample timestamp this node has received on this edge
          (per parent edge; a join keeps one per parent; below a join,
          the newest timestamp among the run's inputs). Monotonic per edge
          (newest-wins slots, no duplicate sends); idle renewals repeat it
          and are accepted. 0 = starting/restarting, always accepted.
          Relies on all Sources sharing a clock.
  Lease = C × max(R, W), timed from receipt   (C ≈ 3, ≥ 2). The lease is
          never shorter than C × W, so choose W above the worst expected
          latency spikes on network edges (e.g. ≥ 50 ms); a flap only costs
          ~one cycle (next demand reactivates the edge with an immediate
          send).
  Demands double as heartbeats.

Leaf:
  on run start:          send Best = R, Worst = R, Phase = R
  idle after a demand:   wait W, then resend every max(R, W), same Seq, Phase 0
  at startup:            send immediately, then every max(R, W), Seq 0,
                         Phase 0, until data arrives

Interior node: never wait for all children. Store each child's latest
demand on its edge with its receive time; on any child demand, recompute
from all live stored demands:
  c*    = live child with smallest max(Best, Worst)   (fastest consumer)
  Phase_c*(now) = stored Phase_c* − (now − received_c*); if negative,
                  + k·I_c* to the next occurrence still ahead
                  (a stored Phase is a duration from its receipt: age it)
  Best  = Best_c*
  Worst = max(Worst_c*, R)
  Phase = Phase_c*(now) − R
  Send up only when the result can have changed: the demand came from c*,
  c* changed, or the set of live children changed (c*'s run-start demands
  keep the parent's lease renewed). At startup, send on the first child's
  demand alone.
  Join node: send the same demand to every parent. Each branch clamps and
    phase-shifts it normally on the way up, so branch results arrive at the
    join together. At the split point the branches are ordinary independent
    children (no grouping). Joined inputs are the newest per branch, not the
    same sample (see Node contract).

Receiving side of edge e (parent, or Source):
  0. network edge (sending side): Phase −= RTT
     (T was already folded in by the receiving side)
  1. discard if Seq < stored Seq_e, unless Seq = 0
  2. I = max(Best, Worst)                    (Source: also ≥ P)
  3. lease_end = now + Lease
  4. Source (timer-driven; the only place Phase sets a send time):
       e has no lease:  send the newest frame now
       e has a lease:   next send = now + Phase + k·I (smallest k still ahead)
       repeat every I until lease_end
     Interior node (output-driven): when an output is ready, send it on each
       leased edge whose last send was ≥ I − ε ago (ε ≈ jitter); otherwise
       hold it in that edge's slot (newer replaces older) and send it once
       I − ε has passed. Never send the same output twice on an edge.
  5. lease expiry or termination on e: stop sending on e;
     all children terminated or expired → send termination to all parents
     otherwise → send an updated demand

On receiving data:
  non-join: run when the input arrives
  join: fill the input's slot (newer replaces older); the first input
    since the last run starts G. Run when all slots are full, or at G with
    whatever is present (partial); G unset → wait for all slots. Late
    inputs are not filtered: a late input opens a new cycle and can cause
    an extra partial run on stale data (accepted in v1; per-edge send
    limits keep children from being oversupplied).
  at each run start: record slot_wait / idle; a leaf sends a new demand;
  on finish: update R
```

Pitfalls (from draft reviews): `Worst = R + T` (link and engine run in parallel: use max);
subtracting only T at network hops (also RTT: the demand's trip up plus the
data's trip down); estimating T from bytes ÷ elapsed time (paced frames
leave the link idle, so this measures the send rate, not capacity) or from
max bandwidth (too optimistic for a committed pace); relying on the QUIC
stack's congestion-control stats (pacing rate, BBR bandwidth, cwnd) — not
available under every controller; omitting the node's own R from `Worst`; taking min `Best`
and min `Worst` separately (can produce an interval no child needs; take one
child's Best, Worst and Phase together); timer-driven sends at interior
nodes alongside output-driven forwarding (pick one: Source timed, interior
output-driven); dropping an output that's ready slightly before I (hold it;
compare against I − ε); assuming a join's inputs share a sample; starting
a join's grace on the first input ever (it must restart after each run);
using Source sample IDs as Seq (not comparable across Sources;
ambiguous below a join; v1 uses the newest sample timestamp, valid because
all Sources share a clock); detecting a join's late inputs by comparing
sample timestamps across branches (the slow branch is sent an older sample
by design, so when the branch latency difference exceeds I, every on-time
slow input looks late and the branch is never used);
propagating a termination while another child is still live; idle demands
with Phase = R instead of 0; leaf sending only on run start (idle deadlock);
forwarding child demands instead of recomputing; waiting for every
child's demand before sending up (a slow or dead child stalls the fast
one); reusing a stored child Phase without aging it by the time since it
was received; anchoring the lease at
Phase. A `source` field is unnecessary: demand is routed per edge.

Known v1 limitations: slow engines (large R) detect failure slowly while
busy; a late join input opens a new cycle and can trigger an extra
partial run on stale data; joined inputs can come from different samples (skew ≈ branch latency
difference); a join with G unset fires at its slowest branch's pace but
demands at its own R, so a faster branch is overrun (waste; with G set
there is no overrun); at a fan-out, a slower child gets the first output
after its I elapses, not one timed to its phase (it may wait up to the fast
child's interval in its slot); filters reduce downstream
throughput to ≈ pass rate × capacity.

## Node contract (v1)

Every run produces at most one output, sent (per schedule) to all children.

```python
@dataclass
class NodeContract:
    inputs: Single | Join = Single()

@dataclass
class Join:
    parents: list[str]
    grace: float | None = None   # set: fire G after first arrival (or when all
                                 # full); None: wait for all slots
```

- Engines return `RunResult(output: payload | None)`; `None` = filter
  rejection. Filters are otherwise plain engines (no pass-rate prediction).
- Items carry lineage (source, sample ID, sample timestamp); joins merge it.
- **Join (newest-per-branch, read-once):** one slot per parent.
  G set: fire G after the first (fastest) branch delivers in the current
  cycle, or immediately once every slot is full. Late inputs are kept
  (stale input is acceptable in v1) and can trigger an extra partial run.
  The join runs at about the
  fastest branch's pace; a slower branch's result is used on the next run
  after it arrives. Runs missing some inputs are **partial**: normal when
  branches run at different paces, not only on faults; tag them with which
  inputs were present. Every partial run costs the full G of latency, so
  keep G near network jitter.
  G unset (`None`): wait until every slot is full (slowest branch's pace);
  blocks if a branch is down.
  Either way: take (and empty) every full slot; values are never reused.
  Inputs need not share a sample (each branch sends its own newest output);
  lineage lets the join or consumers check the skew and mark or drop runs
  where it's too large (sample timestamps are directly comparable: all
  Sources share a clock).
- A filter rejection on one branch leaves that slot empty for the cycle:
  the join waits (G unset) or runs partial after G.

## Evaluating the design

- **Metrics:** throughput (results/s); end-to-end latency (result arrival at
  the Source − timestamp of its sample); wasted runs; wasted transfers;
  per-node slot wait and idle time (persistent one-sided values reveal
  timing bias, the trigger for adding M); lease flaps (expiry followed
  shortly by a new demand on the same edge; the trigger for adding J); and
  time-averaged **age of information** (age of the newest result the Source
  holds). Latency alone misleads: few in-flight inputs give low per-result
  latency but stale information most of the time.
- **Baselines:** push (publish every sample) and one-ahead pull.
- **Real-protocol testing:** keep scheduler logic behind a transport
  interface (send data, send control, callbacks, clock, timers) so the same
  code runs simulated and real. QUIC first, then iceoryx2 (check subscriber
  buffer size / overflow gives newest-wins). Emulate the network with network
  namespaces + veth + `tc netem`; keep the rate limiter's queue small or it
  adds the bufferbloat being tested. Dummy engines = separate processes
  sleeping for random service times.

## Deferred (designed, not in v1)

- **Filter pass-rate prediction:** scale Best/Worst by pass rate p so a
  filter requests ~1/p inputs per output.
- **Join demand from observed firing rate** (only needed for G unset): join sends
  `Best = max(R, low-percentile interval between its runs)` so a fast
  branch isn't overrun; use a low percentile, not the mean, or jitter makes
  the demanded interval creep upward each renewal.
- **Strict sample-matched join** (for inputs that must describe the same
  instant): demands tagged with a JoinID; where they meet, shared interval
  `max(min Best, max Worst)`; each group edge keeps its own Phase, but the
  earliest-sending edge picks the sample and every group edge sends that
  same sample at its own time (lagging edges hold ≤ ~ceil(Δphase / I)
  pinned samples). Sending the sample on all edges at the same moment fails
  when branch latencies differ by more than I (the fast branch overwrites
  the join's slot first).
- **Join cycle gate** (add if many partial runs follow late inputs):
  gate = last run start + I/2 on the join's own clock; inputs before it are
  late for the previous cycle → discard (or hold without opening a cycle);
  inputs after it open the next cycle and start G. Avoids the extra partial
  run a late input otherwise triggers.
- **Other join modes:** `latest` with a designated trigger parent and slower
  refresh demands to the others; `approx` (target sample time across
  different Sources).
- **Phase correction M** (add only if slot-wait / idle metrics show
  persistent one-sided bias, e.g. a node idling > a few % of I, or a remote
  edge consistently late by a similar amount; likely cause: asymmetric link
  latency, so per-edge correction may suffice): `M = k × (slot_wait − idle)`,
  `Phase += M` at each hop. Signed (idle must make Phase smaller; adding
  idle makes inputs later and runs away; idle-only never corrects early
  arrivals); slot_wait / idle, not time between run starts (that's ≈ I);
  a join's grace wait is not idle; Σk along any path < 1 (corrections add up
  per hop); lower gain on remote edges (feedback is an RTT old). It fixes
  only bias: v1 already re-anchors Phase with a fresh R on every run-start
  demand, and jitter can't be fed back away.
- **Fan-out run planning:** coalescing windows `w_c` and harmonic snapping of
  child intervals to integer multiples (avoids beat-frequency misalignment).
- **Incarnation IDs** for demand ordering; v1 uses Seq 0 = restart.
- **Lease jitter allowance J** (add if lease flaps are frequent: metric =
  lease expiries followed shortly by a new demand on the same edge):
  `Lease += J`, J = p99(RTT) − min(RTT) from the pings. v1 relies on W ≥
  expected latency spikes instead.
- **Heartbeat refinements:** cap the heartbeat period for slow engines,
  event-driven renewals, RTT-based minimum renewal period, parent-side lease
  flooring from observed renewal gaps.
- **Network waste controls:** receiver-slot overwrite reports raising Worst;
  pacing releases slightly below measured capacity; coalescing releases that
  share a link.
- **Diagnostics:** `limited_by` field naming the stage that set Worst;
  `Best < Worst` = capacity-limited path.
- **Routers and expanders** (content-based routing, one-to-many outputs):
  few use cases currently.
