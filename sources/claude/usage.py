"""Claude consumer-plan usage: fetch + parse the internal /api/oauth/usage
endpoint (the same one Claude Code itself calls).

`parse_usage()` is pure and unit-tested. `fetch_usage()` does the network I/O
and must run on a worker thread. We only ever GET — never mutate anything.
This is an undocumented internal endpoint; parse defensively so a schema
change degrades to "no windows" rather than crashing the app.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import requests

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_BASE_HEADERS = {"anthropic-version": "2023-06-01"}


@dataclass
class UsageWindow:
    key: str                       # "session" | "weekly_all" | "weekly_scoped" | "weekly_opus"
    label: str                     # human label, e.g. "5h session"
    utilization: float             # 0..100
    resets_at: Optional[datetime] = None
    severity: str = "normal"       # "normal" | "warning" | "critical"


@dataclass
class ClaudeUsage:
    windows: List[UsageWindow] = field(default_factory=list)

    def primary(self) -> Optional[UsageWindow]:
        """The binding window (the 5-hour session is the usual headline)."""
        return self.windows[0] if self.windows else None


def _parse_dt(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _norm_severity(s) -> str:
    s = (s or "normal").lower()
    if s in ("rejected", "critical"):
        return "critical"
    if s in ("allowed_warning", "warning"):
        return "warning"
    return "normal"


# (response key, internal kind used in the `limits` array, display label)
_WINDOW_SPECS = [
    ("five_hour", "session", "5h session"),
    ("seven_day", "weekly_all", "7d all"),
    ("seven_day_sonnet", "weekly_scoped", "7d Sonnet"),
    ("seven_day_opus", "weekly_opus", "7d Opus"),
]


def parse_usage(data: dict) -> ClaudeUsage:
    """Pure: turn the /api/oauth/usage JSON into display-ready windows.

    Severity is taken from the `limits` array (keyed by `kind`) when present,
    else "normal". Windows whose payload is null/absent are skipped.
    """
    data = data or {}
    sev_by_kind = {}
    for lim in (data.get("limits") or []):
        if isinstance(lim, dict) and lim.get("kind"):
            sev_by_kind[lim["kind"]] = _norm_severity(lim.get("severity"))

    windows: List[UsageWindow] = []
    for resp_key, kind, label in _WINDOW_SPECS:
        w = data.get(resp_key)
        if not isinstance(w, dict):
            continue
        util = w.get("utilization")
        if util is None:
            continue
        try:
            util = float(util)
        except (ValueError, TypeError):
            continue
        windows.append(UsageWindow(
            key=kind,
            label=label,
            utilization=util,
            resets_at=_parse_dt(w.get("resets_at")),
            severity=sev_by_kind.get(kind, "normal"),
        ))
    return ClaudeUsage(windows=windows)


def fetch_usage(access_token: str, timeout: float = 12.0) -> Optional[dict]:
    """GET the usage endpoint. Returns the JSON dict, or None on any failure
    (network, non-200, bad JSON). Never raises. Read-only."""
    try:
        headers = dict(_BASE_HEADERS)
        headers["Authorization"] = f"Bearer {access_token}"
        resp = requests.get(USAGE_URL, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None
