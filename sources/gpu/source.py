"""The NVIDIA GPU source — a peer to OpenRouter/Claude on the same contract.

poll() reads NVML on the worker thread; build_card() runs on the main thread.
is_available() is False on non-NVIDIA machines (and when hidden via settings),
so the source simply doesn't appear.
"""
from __future__ import annotations

from sources.base import Source
from sources.gpu.nvml import NvmlReader


class GpuSource(Source):
    source_id = "gpu"
    display_name = "GPU"
    accent = "#76B900"
    poll_interval = 3  # live stats; cheap NVML reads

    def __init__(self, settings=None):
        self._settings = settings
        self._reader = NvmlReader()

    def is_available(self) -> bool:
        if self._settings is not None and not getattr(self._settings, "show_gpu", True):
            return False
        return self._reader.available()

    def poll(self):
        # Returns a GpuStats or None (card renders an "unavailable" line on None).
        return self._reader.read()

    def severity(self, data) -> str:
        t = getattr(data, "temp", None) if data is not None else None
        if t is None:
            return "normal"
        if t >= 85:
            return "critical"
        if t >= 72:
            return "warning"
        return "normal"

    def build_card(self, parent=None):
        from sources.gpu.card import GpuCard
        return GpuCard(parent)
