"""Persist the last *good* Claude usage reading so the card shows it (stamped
"as of …") immediately on launch, instead of blanking until the first
successful fetch lands. Read and written by ClaudeSource only.

This is strictly local display state in %APPDATA%/Pulse/claude_usage.json — no
credentials, no tokens, nothing secret. Tolerant of a missing/corrupt file
(returns None), and writes atomically (temp + replace).
"""
from __future__ import annotations

import json
from typing import Optional, Tuple

from persistence import state_dir
from sources.claude.usage import ClaudeUsage, UsageWindow, _parse_dt


def _path():
    return state_dir() / "claude_usage.json"


def save_usage(usage: ClaudeUsage, fetched_at: float) -> None:
    """Best-effort persist; never raises."""
    try:
        payload = {
            "fetched_at": float(fetched_at),
            "windows": [
                {
                    "key": w.key,
                    "label": w.label,
                    "utilization": w.utilization,
                    "resets_at": w.resets_at.isoformat() if w.resets_at else None,
                    "severity": w.severity,
                }
                for w in usage.windows
            ],
        }
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def load_usage() -> Tuple[Optional[ClaudeUsage], Optional[float]]:
    """Return (ClaudeUsage, fetched_at_epoch) or (None, None). Never raises."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, ValueError):
        return None, None
    if not isinstance(raw, dict):
        return None, None

    windows = []
    for w in raw.get("windows") or []:
        if not isinstance(w, dict):
            continue
        try:
            windows.append(
                UsageWindow(
                    key=str(w.get("key", "")),
                    label=str(w.get("label", "")),
                    utilization=float(w.get("utilization", 0.0)),
                    resets_at=_parse_dt(w.get("resets_at")),
                    severity=str(w.get("severity", "normal")),
                )
            )
        except (ValueError, TypeError):
            continue
    if not windows:
        return None, None

    fetched_at = raw.get("fetched_at")
    try:
        fetched_at = float(fetched_at) if fetched_at is not None else None
    except (ValueError, TypeError):
        fetched_at = None
    return ClaudeUsage(windows=windows), fetched_at
