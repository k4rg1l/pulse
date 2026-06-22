"""System vitals via psutil (CPU %, RAM, network up/down rates).

`SystemStats` + derived props are pure and unit-tested. `SystemReader` does
the psutil I/O on the worker thread: it primes cpu_percent once (the first
call always returns 0) and derives network rates from counter deltas over
wall-clock, clamping negative deltas (adapter resets) to zero. No admin.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class SystemStats:
    cpu: float = 0.0          # percent 0..100
    ram_used: int = 0         # bytes
    ram_total: int = 0        # bytes
    net_up: float = 0.0       # bytes/sec
    net_down: float = 0.0     # bytes/sec

    @property
    def ram_used_gb(self) -> float:
        return self.ram_used / 1e9

    @property
    def ram_total_gb(self) -> float:
        return self.ram_total / 1e9

    @property
    def ram_percent(self) -> float:
        return (self.ram_used / self.ram_total * 100.0) if self.ram_total else 0.0


def net_rate(prev: Optional[Tuple[float, int, int]], now_ts: float,
             sent: int, recv: int) -> Tuple[float, float]:
    """Pure: bytes/sec up/down from the previous (ts, sent, recv) sample.
    Returns (0, 0) with no baseline; clamps negative deltas to zero."""
    if not prev:
        return 0.0, 0.0
    dt = now_ts - prev[0]
    if dt <= 0:
        return 0.0, 0.0
    up = max(0.0, (sent - prev[1]) / dt)
    down = max(0.0, (recv - prev[2]) / dt)
    return up, down


class SystemReader:
    def __init__(self):
        self._primed = False
        self._last_net: Optional[Tuple[float, int, int]] = None

    def available(self) -> bool:
        try:
            import psutil  # noqa: F401
            return True
        except Exception:
            return False

    def read(self) -> Optional[SystemStats]:
        try:
            import psutil
        except Exception:
            return None
        try:
            if not self._primed:
                psutil.cpu_percent(interval=None)  # prime (first call is 0)
                self._primed = True
            cpu = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            net = psutil.net_io_counters()
            now = time.time()
            up, down = net_rate(self._last_net, now, net.bytes_sent, net.bytes_recv)
            self._last_net = (now, net.bytes_sent, net.bytes_recv)
            return SystemStats(cpu=float(cpu), ram_used=int(vm.used),
                               ram_total=int(vm.total), net_up=up, net_down=down)
        except Exception:
            return None
