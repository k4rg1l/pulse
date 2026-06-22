"""Unit tests for the Claude usage parser (sources/claude/usage.py).

Payloads mirror the live /api/oauth/usage response shape (verified against
the real endpoint and the reverse-engineering notes). Pure parsing only — no
network; fetch_usage() is exercised by manual E2E (see docs/TESTING.md).
"""
from datetime import datetime, timezone

from sources.claude.credentials import ClaudeCredentials
from sources.claude.usage import parse_usage

# A representative live-shaped payload (5h 15%, 7d 10%, Sonnet 0%, no Opus).
SAMPLE = {
    "five_hour": {"utilization": 15.0, "resets_at": "2026-06-22T05:30:00.144062+00:00"},
    "seven_day": {"utilization": 10.0, "resets_at": "2026-06-24T11:00:00.144084+00:00"},
    "seven_day_sonnet": {"utilization": 0.0, "resets_at": "2026-06-24T11:00:00.144092+00:00"},
    "seven_day_opus": None,
    "limits": [
        {"kind": "session", "percent": 15, "severity": "normal", "is_active": True},
        {"kind": "weekly_all", "percent": 10, "severity": "allowed_warning", "is_active": False},
        {"kind": "weekly_scoped", "percent": 0, "severity": "normal", "is_active": False},
    ],
    "spend": {"enabled": False, "percent": 0},
}


def test_parses_the_present_windows_and_skips_null():
    usage = parse_usage(SAMPLE)
    keys = [w.key for w in usage.windows]
    assert keys == ["session", "weekly_all", "weekly_scoped"]  # opus (null) skipped


def test_utilization_values():
    by_key = {w.key: w for w in parse_usage(SAMPLE).windows}
    assert by_key["session"].utilization == 15.0
    assert by_key["weekly_all"].utilization == 10.0
    assert by_key["weekly_scoped"].utilization == 0.0


def test_primary_is_the_session_window():
    assert parse_usage(SAMPLE).primary().key == "session"


def test_severity_comes_from_limits_and_is_normalized():
    by_key = {w.key: w for w in parse_usage(SAMPLE).windows}
    assert by_key["session"].severity == "normal"
    assert by_key["weekly_all"].severity == "warning"   # "allowed_warning" -> "warning"
    assert by_key["weekly_scoped"].severity == "normal"


def test_rejected_maps_to_critical():
    data = {
        "five_hour": {"utilization": 99.0, "resets_at": None},
        "limits": [{"kind": "session", "severity": "rejected"}],
    }
    assert parse_usage(data).windows[0].severity == "critical"


def test_resets_at_parsed_as_tz_aware_datetime():
    w = parse_usage(SAMPLE).windows[0]
    assert isinstance(w.resets_at, datetime)
    assert w.resets_at.tzinfo is not None
    assert w.resets_at == datetime(2026, 6, 22, 5, 30, 0, 144062, tzinfo=timezone.utc)


def test_empty_and_garbage_inputs_dont_crash():
    assert parse_usage({}).windows == []
    assert parse_usage(None).windows == []
    assert parse_usage({"five_hour": {"utilization": None}}).windows == []
    # Missing limits array -> default severity normal, still parses the window.
    one = parse_usage({"five_hour": {"utilization": 5.0}})
    assert one.windows[0].severity == "normal"


def test_credentials_expiry_uses_milliseconds():
    import time
    now_ms = time.time() * 1000
    fresh = ClaudeCredentials(access_token="t", expires_at_ms=int(now_ms + 3_600_000))
    dead = ClaudeCredentials(access_token="t", expires_at_ms=int(now_ms - 1_000))
    assert not fresh.is_expired
    assert dead.is_expired
