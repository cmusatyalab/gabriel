"""Run-time and network-edge estimators (protocol.txt: R, RTT, T)."""

from collections import deque
from statistics import median


def percentile(values, q: float) -> float:
    """Nearest-rank percentile, q in [0, 100]."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    k = max(0, min(len(ordered) - 1, round(q / 100 * (len(ordered) - 1))))
    return ordered[k]


class RunTime:
    """Rolling window of engine run times, seeded until real runs arrive."""

    def __init__(self, seed: float, window: int = 32):
        self._seed = seed
        self._runs: deque[float] = deque(maxlen=window)

    def update(self, seconds: float):
        self._runs.append(seconds)

    def pct(self, q: float) -> float:
        return percentile(self._runs, q) if self._runs else self._seed

    _pct = pct

    @property
    def p20(self) -> float:
        return self._pct(20)

    @property
    def p50(self) -> float:
        return self._pct(50)

    @property
    def p80(self) -> float:
        return self._pct(80)


class Rtt:
    """Median of recent ping round trips, measured by the side that sends
    data (pings share packet scheduling with data frames, so the median is
    robust where min underestimates and max overreacts)."""

    def __init__(self, window: int = 15):
        self._samples: deque[float] = deque(maxlen=window)

    def update(self, seconds: float):
        self._samples.append(seconds)

    @property
    def value(self) -> float:
        return median(self._samples) if self._samples else 0.0


class Transmit:
    """Per-frame link transmit time T, measured at the receiving side as
    last-byte - first-byte arrival. T = frame size x pq(time per byte), where
    q is Params.transmit_pct (protocol.txt: 90)."""

    def __init__(self, window: int = 32):
        self._per_byte: deque[float] = deque(maxlen=window)
        self._sizes: deque[int] = deque(maxlen=window)

    def update(self, size: int, seconds: float):
        if size <= 0:
            return
        self._per_byte.append(seconds / size)
        self._sizes.append(size)

    def at(self, q: float) -> float:
        if not self._sizes:
            return 0.0
        return median(self._sizes) * percentile(self._per_byte, q)

    @property
    def value(self) -> float:
        return self.at(90)


class Period:
    """Frame period P from arrival timestamps (median of recent gaps)."""

    def __init__(self, nominal: float, window: int = 16):
        self._nominal = nominal
        self._last: float | None = None
        self._gaps: deque[float] = deque(maxlen=window)

    def arrival(self, t: float):
        if self._last is not None:
            self._gaps.append(t - self._last)
        self._last = t

    @property
    def value(self) -> float:
        return median(self._gaps) if self._gaps else self._nominal
