"""The Claude source: ties credentials + usage + local token accounting into
one pollable, self-rendering unit (a peer to OpenRouter).

poll() runs on a worker thread and is strictly read-only with respect to the
Claude credentials file (it never refreshes/rotates the token — see
credentials.py). build_card() runs on the main thread.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sources.base import Source
from sources.claude.credentials import credentials_exist, read_credentials
from sources.claude.jsonl import TokenStats, aggregate_recent
from sources.claude.usage import ClaudeUsage, fetch_usage, parse_usage

_WEEK_SECONDS = 7 * 86400


@dataclass
class ClaudeCardData:
    available: bool = True
    subscription: str = ""               # "max" | "pro" | ...
    usage: Optional[ClaudeUsage] = None  # None when stale/unavailable
    tokens: Optional[TokenStats] = None  # local 7-day token accounting
    stale: bool = False                  # token expired or usage fetch failed
    error: Optional[str] = None


class ClaudeSource(Source):
    source_id = "claude"
    display_name = "Claude"
    poll_interval = 60  # usage endpoint is safe at 60s

    def __init__(self, settings=None):
        self._settings = settings
        # Per-file aggregate cache (worker-thread only) so unchanged
        # transcripts aren't re-parsed every poll.
        self._jsonl_cache: dict = {}

    def is_available(self) -> bool:
        # Auto-detect: show iff Claude Code's credentials exist AND the user
        # hasn't hidden the source via settings (show_claude).
        if self._settings is not None and not getattr(self._settings, "show_claude", True):
            return False
        return credentials_exist()

    def poll(self) -> ClaudeCardData:
        """WORKER thread. Read-only; never raises (errors land in the data)."""
        data = ClaudeCardData()
        creds = read_credentials()
        if creds is None:
            data.available = False
            data.error = "Claude credentials not found"
            return data
        data.subscription = creds.subscription_type

        # Usage — only while the token is valid. We NEVER refresh/rotate it;
        # Claude Code owns that lifecycle. Expired -> degrade to "stale".
        if creds.is_expired:
            data.stale = True
        else:
            raw = fetch_usage(creds.access_token)
            if raw is not None:
                data.usage = parse_usage(raw)
            else:
                data.stale = True

        # Local token accounting (no auth, always works).
        try:
            data.tokens = aggregate_recent(time.time() - _WEEK_SECONDS, cache=self._jsonl_cache)
        except Exception:
            data.tokens = None
        return data

    def build_card(self, parent=None):
        # Imported lazily so poll-side code stays Qt-free.
        from sources.claude.card import ClaudeCard
        return ClaudeCard(parent)
