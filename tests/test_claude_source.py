"""Tests for the rate-limit-resilient Claude usage path:
fetch classification, the source's cache/backoff/status state machine, manual
force-refresh, and the on-disk last-good usage store.

All pure/mocked — no live network (per docs/TESTING.md). The autouse
`isolate_appdata` fixture (conftest) points the usage store at a temp dir.
"""
from __future__ import annotations

import time

from sources.claude import source as src_mod
from sources.claude import usage as usage_mod
from sources.claude import usage_store
from sources.claude.source import ClaudeSource
from sources.claude.credentials import ClaudeCredentials
from sources.claude.usage import ClaudeUsage, UsageWindow, UsageFetch


# --------------------------------------------------------------------------
#  fetch_usage classification
# --------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, code, body=None, headers=None):
        self.status_code = code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._body


def _set_get(monkeypatch, code, body=None, headers=None):
    monkeypatch.setattr(usage_mod.requests, "get",
                        lambda *a, **k: _FakeResp(code, body, headers))


def test_fetch_ok(monkeypatch):
    _set_get(monkeypatch, 200, {"five_hour": {"utilization": 5.0}})
    r = usage_mod.fetch_usage("t")
    assert r.kind == "ok" and r.data


def test_fetch_429_is_rate_limited(monkeypatch):
    _set_get(monkeypatch, 429, headers={"Retry-After": "0"})
    assert usage_mod.fetch_usage("t").kind == "rate_limited"


def test_fetch_401_403_is_auth(monkeypatch):
    _set_get(monkeypatch, 401)
    assert usage_mod.fetch_usage("t").kind == "auth"
    _set_get(monkeypatch, 403)
    assert usage_mod.fetch_usage("t").kind == "auth"


def test_fetch_other_and_network_are_unavailable(monkeypatch):
    _set_get(monkeypatch, 500)
    assert usage_mod.fetch_usage("t").kind == "unavailable"

    def boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(usage_mod.requests, "get", boom)
    assert usage_mod.fetch_usage("t").kind == "unavailable"


# --------------------------------------------------------------------------
#  ClaudeSource state machine
# --------------------------------------------------------------------------

def _creds(expired=False):
    exp = (time.time() - 3600) * 1000 if expired else (time.time() + 3600) * 1000
    return ClaudeCredentials(access_token="tok", expires_at_ms=int(exp),
                             subscription_type="max")


def _patch(monkeypatch, *, creds, fetch, tokens=None):
    monkeypatch.setattr(src_mod, "read_credentials", lambda *a, **k: creds)
    monkeypatch.setattr(src_mod, "fetch_usage", fetch)
    monkeypatch.setattr(src_mod, "aggregate_recent", lambda *a, **k: tokens)


def _ok_payload():
    return {"five_hour": {"utilization": 12.0, "resets_at": "2099-01-01T00:00:00+00:00"}}


def test_live_then_cached_without_refetch(monkeypatch):
    calls = []

    def fetch(tok):
        calls.append(tok)
        return UsageFetch("ok", data=_ok_payload())

    _patch(monkeypatch, creds=_creds(), fetch=fetch)
    s = ClaudeSource()
    d1 = s.poll()
    assert d1.usage_status == "live"
    assert d1.usage and d1.usage.windows
    # Second poll within the 120s cooldown: cached, retained, NO refetch.
    d2 = s.poll()
    assert d2.usage_status == "cached"
    assert d2.usage and d2.usage.windows
    assert len(calls) == 1


def test_rate_limited_keeps_last_good(monkeypatch):
    seq = [UsageFetch("ok", data=_ok_payload()), UsageFetch("rate_limited")]
    _patch(monkeypatch, creds=_creds(), fetch=lambda t: seq.pop(0))
    s = ClaudeSource()
    s.poll()                 # live, caches
    s._usage_next_at = 0.0   # allow the next fetch
    d = s.poll()             # 429
    assert d.usage_status == "cached"
    assert d.usage and d.usage.windows           # still shows last-good
    assert d.usage_age_seconds is not None
    assert s._usage_failures == 1
    assert s._usage_next_at > time.monotonic()   # backed off


def test_auth_failure_reports_expired(monkeypatch):
    _patch(monkeypatch, creds=_creds(), fetch=lambda t: UsageFetch("auth"))
    s = ClaudeSource()
    assert s.poll().usage_status == "expired"


def test_expired_token_never_hits_network(monkeypatch):
    calls = []
    _patch(monkeypatch, creds=_creds(expired=True),
           fetch=lambda t: calls.append(t) or UsageFetch("ok", data=_ok_payload()))
    s = ClaudeSource()
    assert s.poll().usage_status == "expired"
    assert calls == []


def test_no_creds_unavailable(monkeypatch):
    _patch(monkeypatch, creds=None, fetch=lambda t: UsageFetch("ok", data=_ok_payload()))
    s = ClaudeSource()
    d = s.poll()
    assert d.available is False
    assert d.usage_status == "unavailable"


def test_force_refresh_breaks_backoff(monkeypatch):
    calls = []

    def fetch(tok):
        calls.append(tok)
        return UsageFetch("ok", data=_ok_payload()) if len(calls) == 1 else UsageFetch("rate_limited")

    _patch(monkeypatch, creds=_creds(), fetch=fetch)
    s = ClaudeSource()
    s.poll()                      # call 1 (live); next_at now far in the future
    s._last_usage_at -= 100       # pretend last success was >10s ago
    s.force_refresh()
    s.poll()                      # forced -> refetch despite the cooldown
    assert len(calls) == 2


def test_force_refresh_skipped_right_after_success(monkeypatch):
    calls = []
    _patch(monkeypatch, creds=_creds(),
           fetch=lambda t: calls.append(t) or UsageFetch("ok", data=_ok_payload()))
    s = ClaudeSource()
    s.poll()                      # call 1
    s.force_refresh()
    s.poll()                      # success <10s ago -> force is a no-op
    assert len(calls) == 1


# --------------------------------------------------------------------------
#  Persistence (last-good usage store)
# --------------------------------------------------------------------------

def test_usage_store_round_trip():
    u = ClaudeUsage(windows=[UsageWindow(key="session", label="5h session",
                                         utilization=12.0, severity="normal")])
    usage_store.save_usage(u, 1234.5)
    loaded, wall = usage_store.load_usage()
    assert loaded is not None and loaded.windows[0].key == "session"
    assert wall == 1234.5


def test_load_missing_is_none():
    loaded, wall = usage_store.load_usage()
    assert loaded is None and wall is None


def test_persisted_cache_warms_card_on_first_throttle(monkeypatch):
    # A previous run persisted usage; a fresh source that immediately 429s
    # should still display it, stamped, instead of blanking.
    u = ClaudeUsage(windows=[UsageWindow(key="session", label="5h session",
                                         utilization=20.0)])
    usage_store.save_usage(u, time.time() - 600)
    _patch(monkeypatch, creds=_creds(), fetch=lambda t: UsageFetch("rate_limited"))
    s = ClaudeSource()
    d = s.poll()
    assert d.usage_status == "cached"
    assert d.usage and d.usage.windows[0].utilization == 20.0
    assert d.usage_age_seconds and d.usage_age_seconds >= 500
