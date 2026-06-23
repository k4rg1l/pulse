"""The Source contract.

Pulse is not OpenRouter — it is a monitor that can show many sources side by
side. A Source is a self-contained, pollable, self-rendering unit:

  * is_available()      -> bool   (MAIN thread; cheap; should we show it?)
  * poll()              -> data   (WORKER thread; does all I/O; returns plain
                                   data; must never touch Qt and never raise)
  * build_card(parent)  -> QWidget with a render(data) method (MAIN thread)

The controller polls each available source on a worker thread on its own
interval and marshals the result back to the card's render() on the main
thread — the same worker -> signal -> main contract the OpenRouter path
already uses. OpenRouter migrates onto this contract incrementally; new
sources (Claude, GPU, …) are built on it from day one, so no single provider
is privileged.

This module is intentionally Qt-free (it only declares the contract); the
concrete sources import Qt for their cards.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class Source(ABC):
    #: stable identifier, e.g. "claude"
    source_id: str = ""
    #: section title shown in the dashboard, e.g. "Claude"
    display_name: str = ""
    #: seconds between polls
    poll_interval: int = 60

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap check (main thread): should this source be shown at all?"""

    @abstractmethod
    def poll(self) -> Any:
        """WORKER thread. Do all I/O here and return a plain data object the
        card knows how to render. Must not touch Qt; should handle its own
        errors (return data carrying an error rather than raising)."""

    @abstractmethod
    def build_card(self, parent: Optional[object] = None) -> Any:
        """MAIN thread. Return a QWidget exposing ``render(data)``."""

    def force_refresh(self) -> None:
        """Optional (MAIN thread): the user asked for an immediate refresh
        (e.g. the dashboard Refresh button). A source with an internal poll
        cooldown/backoff may override this to attempt a fetch on the next
        poll() regardless of its schedule. Default: no-op."""
        return None

    # -- UI metadata (the nav-rail / panel chrome read these; NOT new data) --

    #: per-source accent hex for UI identity (rail, header, themed chrome).
    accent: str = "#7C83FF"

    def severity(self, data) -> str:
        """The at-a-glance health for this source's rail status dot, derived
        from the latest poll `data`: "normal" | "warning" | "critical".
        Default: always normal. Overridden per source."""
        return "normal"
