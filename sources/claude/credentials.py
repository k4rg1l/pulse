"""Read-only access to Claude Code's local OAuth credentials.

CRITICAL SAFETY CONTRACT: this module NEVER writes to, refreshes, or rotates
the credentials file. Claude Code owns the token lifecycle. Refreshing here
would rotate the refresh token and could log the user out of their primary
tool (rotation invalidates the old token). We only READ the current access
token and use it while it is still valid; if it has expired we degrade to a
"stale" state rather than touching the file. See docs/RESEARCH and the
Claude reverse-engineering notes.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def credentials_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


@dataclass
class ClaudeCredentials:
    access_token: str
    expires_at_ms: int          # NOTE: milliseconds, not seconds
    subscription_type: str = ""  # "max" | "pro" | "free" | "team" | ...

    @property
    def is_expired(self) -> bool:
        # expiresAt is in MILLISECONDS. Consider it expired ~60s early so we
        # don't fire a request that 401s mid-flight.
        return (time.time() * 1000) >= (self.expires_at_ms - 60_000)


def read_credentials(path: Optional[Path] = None) -> Optional[ClaudeCredentials]:
    """Return the current Claude credentials, or None if absent/unreadable.

    Read-only; never mutates the file. Returns None (not an exception) on
    any problem so callers can degrade gracefully.
    """
    p = path or credentials_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    oauth = raw.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        return None
    try:
        expires = int(oauth.get("expiresAt", 0) or 0)
    except (ValueError, TypeError):
        expires = 0
    return ClaudeCredentials(
        access_token=str(token),
        expires_at_ms=expires,
        subscription_type=str(oauth.get("subscriptionType", "") or ""),
    )


def credentials_exist(path: Optional[Path] = None) -> bool:
    """Cheap existence check used to auto-detect whether to show the source."""
    return (path or credentials_path()).exists()
