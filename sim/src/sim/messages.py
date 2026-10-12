"""Wire formats.

Data travels on a per-edge data channel as ``u32 header length | JSON header
| payload``. Control messages are small JSON objects on the control channel,
addressed by flow and node (``to``), so the client's router can forward them
without understanding them.
"""

import json
import struct
from dataclasses import dataclass, field

_HEADER_LEN = struct.Struct("<I")

# Control message types
DEMAND = "demand"  # schedule mode: child -> parent
TERM = "term"  # schedule mode: child -> parent, edge no longer wanted
REQUEST = "req"  # pull mode: child -> parent, send me one input
TOKENS = "tok"  # token mode: node or sink -> Source, tokens returned
CREDIT = "credit"  # credit mode: child -> parent, cumulative credits granted
SEG = "seg"  # segment mode: node -> group head, a frame's path was used or dropped
LINK = "link"  # client -> local server: the client <-> remote link went up or down
WANT = "want"  # segment mode: local child -> parent, I'm idle, send me input
PING = "ping"
PONG = "pong"
FLOW = "flow"  # client -> server: create a flow
READY = "ready"  # server -> client: flow created
MARK = "mark"  # client -> server: start of the measurement window
STOP = "stop"  # client -> server: stop the flow and report
REPORT = "report"  # server -> client: per-node metrics
ERROR = "error"


@dataclass
class Item:
    """One value flowing on an edge.

    lineage: [(source, sample_id, sample_ts)] of every sample that went into
        it (joins merge lineages)
    producer: (node, run_id) of the run that made it; the Source's
        (source, sample_id) for frames
    """

    lineage: list[tuple[str, int, float]]
    producer: tuple[str, int]
    payload: bytes = b""
    meta: dict = field(default_factory=dict)

    @property
    def seq(self) -> float:
        """Newest sample timestamp in the lineage (all Sources share a clock)."""
        return max(ts for _, _, ts in self.lineage)

    @property
    def oldest(self) -> float:
        return min(ts for _, _, ts in self.lineage)

    def samples(self) -> list[tuple[str, int]]:
        return [(src, sid) for src, sid, _ in self.lineage]

    def encode(self) -> bytes:
        header = json.dumps(
            {"l": self.lineage, "p": self.producer, "m": self.meta}, separators=(",", ":")
        ).encode()
        return _HEADER_LEN.pack(len(header)) + header + self.payload

    @classmethod
    def decode(cls, raw: bytes) -> "Item":
        (length,) = _HEADER_LEN.unpack_from(raw)
        start = _HEADER_LEN.size
        header = json.loads(raw[start:start + length])
        return cls(
            lineage=[tuple(x) for x in header["l"]],
            producer=tuple(header["p"]),
            payload=raw[start + length:],
            meta=header.get("m", {}),
        )


def merge_lineage(items: list[Item]) -> list[tuple[str, int, float]]:
    seen = {}
    for item in items:
        for src, sid, ts in item.lineage:
            seen[(src, sid)] = ts
    return [(src, sid, ts) for (src, sid), ts in sorted(seen.items())]


def encode_control(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":")).encode()


def decode_control(raw: bytes) -> dict:
    return json.loads(raw)


def demand(flow, frm, to, best, worst, phase, seq, lease) -> dict:
    return {
        "t": DEMAND, "f": flow, "frm": frm, "to": to,
        "best": best, "worst": worst, "phase": phase, "seq": seq, "lease": lease,
    }
