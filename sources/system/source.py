"""System vitals source — a peer on the same contract as the others."""
from __future__ import annotations

from sources.base import Source
from sources.system.vitals import SystemReader


class SystemSource(Source):
    source_id = "system"
    display_name = "System"
    poll_interval = 2  # live vitals

    def __init__(self, settings=None):
        self._settings = settings
        self._reader = SystemReader()

    def is_available(self) -> bool:
        if self._settings is not None and not getattr(self._settings, "show_system", True):
            return False
        return self._reader.available()

    def poll(self):
        return self._reader.read()  # SystemStats or None

    def build_card(self, parent=None):
        from sources.system.card import SystemCard
        return SystemCard(parent)
