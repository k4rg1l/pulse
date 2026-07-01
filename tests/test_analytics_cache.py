"""Regression tests for AnalyticsClient's cache bound (Phase 3 leak fix).

The cache key embeds a now()-based date window, so every poll mints a fresh key;
without a cap the dict grew for the life of the process. These lock the bound and
confirm same-key reuse still works (no network — session.post is monkeypatched).
"""
from api_client import AnalyticsClient


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"data": [], "metadata": {}, "cachedAt": 0}}


def _client(monkeypatch, counter=None):
    c = AnalyticsClient()
    c.unlocked = True  # bypass the locked (no-network) short-circuit

    def _post(*a, **k):
        if counter is not None:
            counter["n"] += 1
        return _Resp()

    monkeypatch.setattr(c.session, "post", _post)
    return c


def test_cache_is_bounded(monkeypatch):
    c = _client(monkeypatch)
    for i in range(AnalyticsClient.CACHE_MAX + 25):   # distinct keys every poll
        c.query(["total_usage"], [], "day", start=f"s{i}", end="e")
    assert len(c._cache) <= AnalyticsClient.CACHE_MAX


def test_same_key_served_from_cache(monkeypatch):
    calls = {"n": 0}
    c = _client(monkeypatch, calls)
    c.query(["total_usage"], [], "day", start="s", end="e")
    c.query(["total_usage"], [], "day", start="s", end="e")  # identical key
    assert calls["n"] == 1  # second call hit the cache, no second POST
