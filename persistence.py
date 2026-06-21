"""
OpenRouter Pulse - Persistence

Stores rolling snapshots of balance / usage to %APPDATA%/OpenRouterPulse/state.json
so the dashboard can show real burn rates and balance-over-time charts that
survive restarts.

A snapshot is captured every time the key/credits endpoints return fresh data.
Snapshots older than RETENTION_DAYS are pruned on save.

This module is intentionally small and dependency-free (stdlib only) so it can
run on the GUI thread without blocking — file I/O on JSON snapshots is well
under a millisecond at this volume.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

RETENTION_DAYS = 90
MAX_SNAPSHOTS = 20_000  # hard cap (~2 weeks at 60s cadence)


def state_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    p = Path(base) / "OpenRouterPulse"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_path() -> Path:
    return state_dir() / "state.json"


@dataclass
class Snapshot:
    ts: float                 # unix seconds, server-agnostic
    total_credits: float      # from /api/v1/credits
    total_usage: float        # from /api/v1/credits
    usage_daily: float        # from /api/v1/key
    usage_monthly: float      # from /api/v1/key

    @property
    def balance(self) -> float:
        return max(0.0, self.total_credits - self.total_usage)


class History:
    """In-memory rolling history of snapshots, persisted to disk."""

    def __init__(self, snapshots: list[Snapshot] | None = None):
        self.snapshots: list[Snapshot] = list(snapshots or [])

    # ------------------------------------------------------------------
    #  Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls) -> "History":
        path = state_path()
        if not path.exists():
            return cls()
        try:
            # utf-8-sig handles BOM if a user opens & re-saves the file
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            snaps = [Snapshot(**s) for s in data.get("snapshots", [])]
            return cls(snaps)
        except Exception as e:
            # Corrupt file — start fresh but don't crash
            print(f"[persistence] load error: {e}; starting fresh")
            return cls()

    def save(self) -> None:
        self._prune()
        payload = {
            "version": 1,
            "snapshots": [asdict(s) for s in self.snapshots],
        }
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(state_path())

    def _prune(self) -> None:
        cutoff = time.time() - RETENTION_DAYS * 86400
        self.snapshots = [s for s in self.snapshots if s.ts >= cutoff]
        if len(self.snapshots) > MAX_SNAPSHOTS:
            self.snapshots = self.snapshots[-MAX_SNAPSHOTS:]

    # ------------------------------------------------------------------
    #  Mutation
    # ------------------------------------------------------------------

    def add(self, snap: Snapshot) -> bool:
        """Append a snapshot if it differs from the latest one.

        Returns True if appended, False if deduped.  Deduping keeps the
        file small when nothing's changing (idle user).
        """
        if self.snapshots:
            last = self.snapshots[-1]
            same_values = (
                abs(last.total_usage - snap.total_usage) < 1e-9
                and abs(last.total_credits - snap.total_credits) < 1e-9
                and abs(last.usage_daily - snap.usage_daily) < 1e-9
            )
            recent = (snap.ts - last.ts) < 300  # < 5min
            if same_values and recent:
                return False
        self.snapshots.append(snap)
        return True

    # ------------------------------------------------------------------
    #  Derived metrics
    # ------------------------------------------------------------------

    def _filter_window(self, seconds: float) -> list[Snapshot]:
        cutoff = time.time() - seconds
        return [s for s in self.snapshots if s.ts >= cutoff]

    def burn_in_window(self, seconds: float) -> float | None:
        """Total $ spent in the last `seconds`, ignoring top-up jumps.

        Returns None if we don't have at least two snapshots in the
        window.  We sum positive deltas in total_usage (which only goes
        up, never down).
        """
        window = self._filter_window(seconds)
        if len(window) < 2:
            return None
        spent = 0.0
        for prev, cur in zip(window, window[1:]):
            d = cur.total_usage - prev.total_usage
            if d > 0:
                spent += d
        return spent

    def burn_rate_per_hour(self, window_seconds: float = 3600) -> float | None:
        """$/hr averaged over the given window (default last hour)."""
        spent = self.burn_in_window(window_seconds)
        if spent is None:
            return None
        # Effective elapsed = min(actual span, window)
        window = self._filter_window(window_seconds)
        if len(window) < 2:
            return None
        span = window[-1].ts - window[0].ts
        if span < 60:  # less than a minute, too jittery
            return None
        return spent * 3600.0 / span

    def topup_events(self, seconds: float | None = None) -> list[tuple[float, float]]:
        """Detect top-ups: snapshots where total_credits jumps up.

        Returns list of (timestamp, amount_added) tuples in the given
        window (or all-time if seconds is None).
        """
        snaps = self._filter_window(seconds) if seconds else self.snapshots
        events: list[tuple[float, float]] = []
        for prev, cur in zip(snaps, snaps[1:]):
            d = cur.total_credits - prev.total_credits
            if d > 0.01:  # >1 cent jump = top-up
                events.append((cur.ts, d))
        return events

    def latest(self) -> Snapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    def first_after(self, seconds_ago: float) -> Snapshot | None:
        cutoff = time.time() - seconds_ago
        for s in self.snapshots:
            if s.ts >= cutoff:
                return s
        return None

    def balance_series(self, seconds: float) -> list[tuple[float, float]]:
        """List of (timestamp, balance) pairs in the last `seconds`."""
        return [(s.ts, s.balance) for s in self._filter_window(seconds)]
