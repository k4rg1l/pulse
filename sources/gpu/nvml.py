"""NVIDIA GPU stats via NVML (nvidia-ml-py).

`GpuStats` + its derived properties are pure and unit-tested. `NvmlReader`
does the live NVML I/O (worker thread); it degrades gracefully on machines
with no NVIDIA driver/GPU (available() -> False, read() -> None) so the source
simply doesn't show. NVML reads need no admin and go through the driver.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class GpuStats:
    name: str = ""
    util: int = 0            # GPU utilization %
    mem_used: int = 0        # bytes
    mem_total: int = 0       # bytes
    temp: int = 0            # °C
    power: Optional[float] = None  # watts (None if the GPU doesn't report it)

    @property
    def mem_used_gb(self) -> float:
        return self.mem_used / 1e9

    @property
    def mem_total_gb(self) -> float:
        return self.mem_total / 1e9

    @property
    def mem_percent(self) -> float:
        return (self.mem_used / self.mem_total * 100.0) if self.mem_total else 0.0

    @property
    def short_name(self) -> str:
        """Trim the vendor prefix: 'NVIDIA GeForce RTX 4070 Ti' -> 'RTX 4070 Ti'."""
        n = self.name
        for prefix in ("NVIDIA GeForce ", "NVIDIA "):
            if n.startswith(prefix):
                return n[len(prefix):]
        return n


class NvmlReader:
    """Lazily initialises NVML once and reads GPU 0. Thread-safe for reads;
    init can run on the main thread (is_available) and reads on the worker."""

    def __init__(self):
        self._ok = False
        self._N = None
        self._handle = None

    def _ensure(self) -> bool:
        if self._ok:
            return True
        try:
            import pynvml as N
            N.nvmlInit()
            self._N = N
            self._handle = N.nvmlDeviceGetHandleByIndex(0)
            self._ok = True
        except Exception:
            self._ok = False
        return self._ok

    def available(self) -> bool:
        return self._ensure()

    def read(self) -> Optional[GpuStats]:
        if not self._ensure():
            return None
        N, h = self._N, self._handle
        try:
            name = N.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode(errors="replace")
            util = N.nvmlDeviceGetUtilizationRates(h)
            mem = N.nvmlDeviceGetMemoryInfo(h)
            temp = N.nvmlDeviceGetTemperature(h, N.NVML_TEMPERATURE_GPU)
            try:
                power = N.nvmlDeviceGetPowerUsage(h) / 1000.0
            except Exception:
                power = None
            return GpuStats(name=str(name), util=int(util.gpu),
                            mem_used=int(mem.used), mem_total=int(mem.total),
                            temp=int(temp), power=power)
        except Exception:
            return None
