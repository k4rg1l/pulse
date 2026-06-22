"""The Claude source: ties credentials + usage + local token accounting into
one pollable, self-rendering unit (a peer to OpenRouter).

poll() runs on a worker thread and is strictly READ-ONLY with respect to the
Claude credentials file — it never refreshes/rotates the token (see
credentials.py). build_card() runs on the main thread.

Usage-endpoint resilience (the foundation fix):
  The internal `/api/oauth/usage` endpoint rate-limits aggressive polling
  (HTTP 429). The token is usually fine; the endpoint just throttles us. So:
    * we fetch usage on a gentle cadence and BACK OFF on 429,
    * we CACHE the last good reading (in memory + on disk) and keep showing it
      with an "as of …" age stamp instead of blanking,
    * we only report "expired / open Claude Code" for a genuine 401 (auth),
    * a manual Refresh (force_refresh) breaks the backoff and retries now.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sources.base import Source
from sources.claude.credentials import credentials_exist, read_credentials
from sources.claude.jsonl import TokenStats, aggregate_recent
from sources.claude.usage import ClaudeUsage, fetch_usage, parse_usage
from sources.claude.usage_store import load_usage, save_usage

_WEEK_SECONDS = 7 * 86400

# Usage-endpoint pacing (seconds). Usage changes slowly, and the endpoint
# throttles us, so we poll it gently and back off hard on 429.
_USAGE_OK_INTERVAL = 120.0       # normal gap between successful fetches
_USAGE_BACKOFF_BASE = 120.0      # first backoff after a throttle
_USAGE_BACKOFF_CAP = 1800.0      # cap backoff at 30 min
_FORCE_MIN_INTERVAL = 10.0       # a manual refresh won't refetch if we just succeeded


@dataclass
class ClaudeCardData:
    available: bool = True
    subscription: str = ""                # "max" | "pro" | ...
    usage: Optional[ClaudeUsage] = None   # last-good (live or cached); None if none yet
    tokens: Optional[TokenStats] = None   # local 7-day token accounting
    # "live"  -> just fetched      "cached"   -> showing last-good after a throttle
    # "expired" -> token dead      "unavailable" -> no data and none cached
    usage_status: str = "unavailable"
    usage_age_seconds: Optional[float] = None  # age of `usage` when cached/stale
    error: Optional[str] = None


class ClaudeSource(Source):
    source_id = "claude"
    display_name = "Claude"
    poll_interval = 60  # source wakes every 60s (cheap JSONL); usage is paced below

    def __init__(self, settings=None):
        self._settings = settings
        # Per-file aggregate cache (worker-thread only) so unchanged
        # transcripts aren't re-parsed every poll.
        self._jsonl_cache: dict = {}
        # Usage cache + backoff state. Worker-thread only, except `_force`
        # which the main thread may set via force_refresh() (a plain bool write).
        self._last_usage: Optional[ClaudeUsage] = None
        self._last_usage_at: Optional[float] = None    # monotonic, last SUCCESS
        self._last_usage_wall: Optional[float] = None  # epoch, last success (for age)
        self._usage_next_at: float = 0.0               # monotonic gate for next fetch
        self._usage_failures: int = 0
        self._force = False
        # Warm the display cache from disk so the card isn't blank on launch.
        try:
            usage, wall = load_usage()
            if usage is not None:
                self._last_usage = usage
                self._last_usage_wall = wall
        except Exception:
            pass

    def is_available(self) -> bool:
        # Auto-detect: show iff Claude Code's credentials exist AND the user
        # hasn't hidden the source via settings (show_claude).
        if self._settings is not None and not getattr(self._settings, "show_claude", True):
            return False
        return credentials_exist()

    def force_refresh(self) -> None:
        """Main thread asks for an immediate usage re-fetch on the next poll
        (e.g. the dashboard/tray Refresh button), bypassing the backoff."""
        self._force = True

    def poll(self) -> ClaudeCardData:
        """WORKER thread. Read-only; never raises (errors land in the data)."""
        data = ClaudeCardData()
        creds = read_credentials()
        if creds is None:
            data.available = False
            data.error = "Claude credentials not found"
            data.usage_status = "unavailable"
            self._attach_tokens(data)
            return data
        data.subscription = creds.subscription_type

        now = time.monotonic()
        forced = self._force
        self._force = False

        if creds.is_expired:
            # Genuinely expired — only running `claude` refreshes it. Don't
            # even hit the network; surface the one action that helps.
            data.usage_status = "expired"
        else:
            due = now >= self._usage_next_at
            if forced:
                recent_success = (
                    self._last_usage_at is not None
                    and (now - self._last_usage_at) < _FORCE_MIN_INTERVAL
                )
                if not recent_success:
                    due = True
            if due:
                data.usage_status = self._fetch_usage(creds.access_token, now)
            else:
                data.usage_status = "cached" if self._last_usage is not None else "unavailable"

        # Attach last-good usage + its age for display (bars stay visible even
        # while cached/expired; the stamp / message conveys freshness).
        if self._last_usage is not None and data.usage_status in ("live", "cached", "expired"):
            data.usage = self._last_usage
            if self._last_usage_wall is not None:
                data.usage_age_seconds = max(0.0, time.time() - self._last_usage_wall)

        self._attach_tokens(data)
        return data

    def _fetch_usage(self, access_token: str, now: float) -> str:
        """Do one usage fetch and update cache/backoff. Returns the status."""
        res = fetch_usage(access_token)
        if res.kind == "ok":
            self._last_usage = parse_usage(res.data)
            self._last_usage_at = now
            self._last_usage_wall = time.time()
            self._usage_failures = 0
            self._usage_next_at = now + _USAGE_OK_INTERVAL
            try:
                save_usage(self._last_usage, self._last_usage_wall)
            except Exception:
                pass
            return "live"
        if res.kind == "auth":
            return "expired"
        # rate_limited | unavailable -> transient; keep last-good, back off.
        self._usage_failures += 1
        backoff = min(
            _USAGE_BACKOFF_CAP,
            _USAGE_BACKOFF_BASE * (2 ** (self._usage_failures - 1)),
        )
        self._usage_next_at = now + backoff
        return "cached" if self._last_usage is not None else "unavailable"

    def _attach_tokens(self, data: ClaudeCardData) -> None:
        # Local token accounting (no auth, always works).
        try:
            data.tokens = aggregate_recent(time.time() - _WEEK_SECONDS, cache=self._jsonl_cache)
        except Exception:
            data.tokens = None

    def build_card(self, parent=None):
        # Imported lazily so poll-side code stays Qt-free.
        from sources.claude.card import ClaudeCard
        return ClaudeCard(parent)
