"""Unit tests for persistence.py — the burn-rate / top-up math and the
snapshot dedup that the whole dashboard forecast rests on.

These are pure-Python (no Qt, no network). Timestamps are built relative
to time.time() because the windowing functions call time.time() live.
"""
import time

import pytest

from persistence import History, Snapshot


def _snap(ts, credits, usage, daily=0.0, monthly=0.0):
    return Snapshot(
        ts=ts,
        total_credits=credits,
        total_usage=usage,
        usage_daily=daily,
        usage_monthly=monthly,
    )


# ---------------------------------------------------------------------------
#  Snapshot.balance
# ---------------------------------------------------------------------------

def test_balance_is_credits_minus_usage():
    assert _snap(0, 20.0, 5.0).balance == pytest.approx(15.0)


def test_balance_never_negative():
    # Usage exceeding credits must clamp to 0, not go negative.
    assert _snap(0, 5.0, 9.0).balance == 0.0


# ---------------------------------------------------------------------------
#  History.add dedup
# ---------------------------------------------------------------------------

def test_add_first_snapshot_always_appended():
    h = History()
    assert h.add(_snap(time.time(), 20.0, 5.0)) is True
    assert len(h.snapshots) == 1


def test_add_dedups_identical_recent_values():
    now = time.time()
    h = History()
    h.add(_snap(now, 20.0, 5.0, daily=5.0))
    # Same values, <5 min later -> deduped (returns False, not stored).
    assert h.add(_snap(now + 60, 20.0, 5.0, daily=5.0)) is False
    assert len(h.snapshots) == 1


def test_add_keeps_changed_values():
    now = time.time()
    h = History()
    h.add(_snap(now, 20.0, 5.0, daily=5.0))
    # total_usage moved -> a real sample, must be kept.
    assert h.add(_snap(now + 60, 20.0, 6.0, daily=6.0)) is True
    assert len(h.snapshots) == 2


def test_add_keeps_identical_values_after_5min_gap():
    now = time.time()
    h = History()
    h.add(_snap(now, 20.0, 5.0, daily=5.0))
    # Identical values but >5 min later -> kept (not a same-instant dupe).
    assert h.add(_snap(now + 400, 20.0, 5.0, daily=5.0)) is True
    assert len(h.snapshots) == 2


# ---------------------------------------------------------------------------
#  Burn rate
# ---------------------------------------------------------------------------

def test_burn_in_window_sums_positive_usage_deltas():
    now = time.time()
    h = History([
        _snap(now - 3000, 20.0, 10.0),
        _snap(now - 1500, 20.0, 12.0),  # +2
        _snap(now - 10, 20.0, 15.0),    # +3
    ])
    assert h.burn_in_window(3600) == pytest.approx(5.0)


def test_burn_in_window_ignores_topup_credit_jumps():
    # A top-up raises total_credits, NOT total_usage, so spend math must be
    # unaffected by the jump.
    now = time.time()
    h = History([
        _snap(now - 3000, 5.0, 10.0),
        _snap(now - 1500, 30.0, 11.0),  # +$25 top-up; usage +1
        _snap(now - 10, 30.0, 13.0),    # usage +2
    ])
    assert h.burn_in_window(3600) == pytest.approx(3.0)


def test_burn_in_window_none_with_under_two_samples():
    now = time.time()
    assert History([_snap(now, 20.0, 10.0)]).burn_in_window(3600) is None
    assert History().burn_in_window(3600) is None


def test_burn_rate_per_hour_extrapolates_to_an_hour():
    now = time.time()
    # $5 spent over an exact 3000s span -> 5 * 3600/3000 = $6.00/hr.
    h = History([
        _snap(now - 3000, 20.0, 10.0),
        _snap(now, 20.0, 15.0),
    ])
    assert h.burn_rate_per_hour(3600) == pytest.approx(6.0, rel=1e-2)


def test_burn_rate_per_hour_none_when_span_too_short():
    now = time.time()
    # Two samples <60s apart are too jittery to extrapolate.
    h = History([
        _snap(now - 20, 20.0, 10.0),
        _snap(now - 10, 20.0, 11.0),
    ])
    assert h.burn_rate_per_hour(3600) is None


# ---------------------------------------------------------------------------
#  Top-up detection
# ---------------------------------------------------------------------------

def test_topup_events_detects_credit_jumps():
    now = time.time()
    h = History([
        _snap(now - 3000, 5.0, 10.0),
        _snap(now - 1500, 30.0, 11.0),  # +$25 top-up here
        _snap(now - 10, 29.0, 12.0),    # normal spend, no jump
    ])
    events = h.topup_events()
    assert len(events) == 1
    ts, amount = events[0]
    assert amount == pytest.approx(25.0)
    assert ts == pytest.approx(now - 1500)


def test_topup_ignores_sub_cent_noise():
    now = time.time()
    h = History([
        _snap(now - 100, 20.0, 5.0),
        _snap(now - 10, 20.005, 5.0),  # <1c jump -> not a top-up
    ])
    assert h.topup_events() == []


# ---------------------------------------------------------------------------
#  Misc accessors
# ---------------------------------------------------------------------------

def test_latest_and_balance_series():
    now = time.time()
    h = History([
        _snap(now - 100, 20.0, 5.0),
        _snap(now - 10, 20.0, 6.0),
    ])
    assert h.latest().total_usage == pytest.approx(6.0)
    series = h.balance_series(3600)
    assert [round(b, 2) for _, b in series] == [15.0, 14.0]


# ---------------------------------------------------------------------------
#  Round-trip persistence (uses isolated APPDATA from conftest)
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip():
    now = time.time()
    h = History([_snap(now, 20.0, 5.0, daily=5.0, monthly=40.0)])
    h.save()
    reloaded = History.load()
    assert len(reloaded.snapshots) == 1
    assert reloaded.snapshots[0].balance == pytest.approx(15.0)


def test_load_missing_file_returns_empty():
    # Isolated APPDATA has no state.json yet.
    assert History.load().snapshots == []


def test_prune_drops_old_snapshots_on_save():
    now = time.time()
    old = now - 200 * 86400  # 200 days, beyond 90-day retention
    h = History([_snap(old, 20.0, 5.0), _snap(now, 20.0, 6.0)])
    h.save()
    reloaded = History.load()
    assert len(reloaded.snapshots) == 1
    assert reloaded.snapshots[0].ts == pytest.approx(now)
