"""Unit tests for the Claude JSONL token aggregator (sources/claude/jsonl.py).

Message shapes mirror the real session transcript schema (assistant message
with message.usage). Pure aggregation only; the file-walking aggregate_recent()
is exercised by manual E2E (see docs/TESTING.md).
"""
import pytest

from sources.claude.jsonl import TokenStats, aggregate_tokens


def _assistant(model, i, o, cr, cc, ws=0, ts=None):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": i,
                "output_tokens": o,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cc,
                "server_tool_use": {"web_search_requests": ws},
            },
        },
    }


def test_sums_all_token_buckets():
    recs = [
        _assistant("claude-opus-4-8", 100, 200, 1000, 50),
        _assistant("claude-opus-4-8", 10, 20, 100, 5),
    ]
    s = aggregate_tokens(recs)
    assert s.input == 110
    assert s.output == 220
    assert s.cache_read == 1100
    assert s.cache_creation == 55
    assert s.total == 110 + 220 + 1100 + 55
    assert s.messages == 2


def test_per_model_breakdown():
    recs = [
        _assistant("claude-opus-4-8", 100, 100, 0, 0),    # 200
        _assistant("claude-sonnet-4-6", 10, 10, 0, 0),    # 20
        _assistant("claude-opus-4-8", 0, 50, 0, 0),       # 50
    ]
    s = aggregate_tokens(recs)
    assert s.by_model["claude-opus-4-8"] == 250
    assert s.by_model["claude-sonnet-4-6"] == 20


def test_cache_efficiency():
    # 900 cache reads vs 100 fresh input -> 90% efficient.
    s = aggregate_tokens([_assistant("m", 100, 0, 900, 0)])
    assert s.cache_efficiency == pytest.approx(0.9)
    # No input at all -> 0 (avoid div-by-zero).
    assert aggregate_tokens([]).cache_efficiency == 0.0


def test_web_searches_counted():
    s = aggregate_tokens([_assistant("m", 1, 1, 0, 0, ws=3), _assistant("m", 1, 1, 0, 0, ws=2)])
    assert s.web_searches == 5


def test_ignores_non_assistant_and_usageless_lines():
    recs = [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "message": {"model": "m"}},   # no usage
        {"type": "queue-operation", "operation": "start"},
        _assistant("m", 5, 5, 0, 0),
    ]
    s = aggregate_tokens(recs)
    assert s.messages == 1
    assert s.input == 5


def test_since_ts_filters_by_timestamp():
    recs = [
        _assistant("m", 100, 0, 0, 0, ts="2026-06-20T10:00:00+00:00"),  # old
        _assistant("m", 7, 0, 0, 0, ts="2026-06-22T10:00:00+00:00"),    # recent
    ]
    cutoff = 1_750_000_000.0  # ~2025; both are after, so both counted
    assert aggregate_tokens(recs, since_ts=cutoff).input == 107
    # Cutoff between the two (2026-06-21):
    from datetime import datetime, timezone
    mid = datetime(2026, 6, 21, tzinfo=timezone.utc).timestamp()
    assert aggregate_tokens(recs, since_ts=mid).input == 7


def test_merge_combines_two_stats():
    a = aggregate_tokens([_assistant("m1", 10, 0, 0, 0)])
    b = aggregate_tokens([_assistant("m2", 5, 0, 0, 0)])
    a.merge(b)
    assert a.input == 15
    assert a.by_model == {"m1": 10, "m2": 5}


def test_malformed_usage_values_dont_crash():
    rec = {"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": None, "output_tokens": "x"}}}
    s = aggregate_tokens([rec])
    assert s.input == 0 and s.output == 0 and s.messages == 1
