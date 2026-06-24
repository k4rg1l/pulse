"""Unit tests for the frontend (no-auth) client parsers — foundation F2.

Pure-logic tests against captured fixtures in tests/fixtures/ (public, global
data from openrouter.ai/api/frontend/*), never the live endpoint (the Sources
contract). The fixtures were captured + trimmed by tools/or_probe_frontend.py
on 2026-06-23.
"""
import json
from pathlib import Path

import pytest

from frontend_client import (
    parse_catalog_permaslugs, PermaslugResolver,
    parse_all_providers, ProviderTrustBook, ProviderTrust,
    parse_performance, SpeedBoard,
    parse_endpoint_refs, EndpointRef,
    parse_uptime_hourly, UptimeHistory,
    provider_slug_from_tag, _norm,
    custody_score, CustodyGrade, TRUST_TIERS,
)

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
#  Permaslug resolver
# ---------------------------------------------------------------------------
def test_resolver_maps_slug_to_permaslug_and_back():
    res = parse_catalog_permaslugs(_load("fe_catalog_slice.json")["data"])
    assert res.permaslug("anthropic/claude-opus-4.8") == "anthropic/claude-4.8-opus-20260528"
    assert res.slug("anthropic/claude-4.8-opus-20260528") == "anthropic/claude-opus-4.8"


def test_resolver_passes_through_a_known_permaslug():
    res = parse_catalog_permaslugs(_load("fe_catalog_slice.json")["data"])
    # If the caller already holds a permaslug, permaslug() returns it unchanged.
    assert res.permaslug("anthropic/claude-4.8-opus-20260528") == "anthropic/claude-4.8-opus-20260528"


def test_resolver_unknown_slug_is_none():
    res = parse_catalog_permaslugs(_load("fe_catalog_slice.json")["data"])
    assert res.permaslug("nobody/nope") is None
    assert res.slug("nobody/nope") is None


def test_resolver_handles_empty_and_malformed_rows():
    res = parse_catalog_permaslugs([{}, {"slug": "a/b"}, {"permaslug": "x"}, None and {}])
    assert len(res) == 0   # rows missing either side are skipped


# ---------------------------------------------------------------------------
#  Provider trust  (all-providers)
# ---------------------------------------------------------------------------
def test_all_providers_parses_policy_fields():
    book = parse_all_providers(_load("fe_all_providers.json")["data"])
    anthropic = book.lookup(slug="anthropic")
    assert anthropic.trains is False
    assert anthropic.retains is True
    assert anthropic.retention_days == 30
    assert anthropic.headquarters == "US"

    deepseek = book.lookup(slug="deepseek")
    assert deepseek.trains is True            # DeepSeek trains on prompts
    assert deepseek.headquarters == "CN"


def test_all_providers_datacenters_and_null_handling():
    book = parse_all_providers(_load("fe_all_providers.json")["data"])
    moonshot = book.lookup(slug="moonshotai")
    assert moonshot.datacenters == ("SG",)    # populated list → tuple
    assert moonshot.datacenters_known is True
    anthropic = book.lookup(slug="anthropic")
    assert anthropic.datacenters == ()         # null datacenters → empty tuple
    assert anthropic.datacenters_known is False   # …but we remember it was null


def test_all_providers_missing_datapolicy_is_safe():
    book = parse_all_providers([{"slug": "x", "name": "X"}])
    p = book.lookup(slug="x")
    assert p.has_policy is False
    assert p.trains is False and p.retains is False and p.retention_days is None


def test_provider_lookup_by_tag_slug_and_name():
    book = parse_all_providers(_load("fe_all_providers.json")["data"])
    # A board endpoint carries a 'slug/region' tag — match on its leading slug.
    assert book.lookup(tag="anthropic/2") is book.lookup(slug="anthropic")
    assert book.lookup(tag="google-vertex/global").slug == "google-vertex"
    # And by normalized display name.
    assert book.lookup(name="Anthropic").slug == "anthropic"
    assert book.lookup(name="Definitely Not A Provider") is None


def test_icon_abs_url_resolves_relative_and_keeps_absolute():
    book = parse_all_providers(_load("fe_all_providers.json")["data"])
    assert book.lookup(slug="anthropic").icon_abs_url == "https://openrouter.ai/images/icons/Anthropic.svg"
    # Together's icon is already an absolute external URL — keep as-is.
    assert book.lookup(slug="together").icon_abs_url.startswith("https://t0.gstatic.com/")


def test_icon_abs_url_handles_protocol_relative_and_missing():
    book = parse_all_providers([
        {"slug": "p1", "name": "P1", "icon": {"url": "//cdn.example.com/a.png"}},
        {"slug": "p2", "name": "P2", "icon": None},
    ])
    assert book.lookup(slug="p1").icon_abs_url == "https://cdn.example.com/a.png"
    assert book.lookup(slug="p2").icon_abs_url is None


def test_provider_slug_from_tag():
    assert provider_slug_from_tag("amazon-bedrock/eu-west-1") == "amazon-bedrock"
    assert provider_slug_from_tag("anthropic") == "anthropic"
    assert provider_slug_from_tag("") == ""


def test_norm_cross_matches_slug_and_display_name():
    assert _norm("amazon-bedrock") == "amazon bedrock"
    assert _norm("Amazon Bedrock") == "amazon bedrock"


# ---------------------------------------------------------------------------
#  The Ledger — Custody Score / trust grade
# ---------------------------------------------------------------------------
def _provider(**kw):
    return ProviderTrust(slug=kw.pop("slug", "p"), name=kw.pop("name", "P"), **kw)


def test_custody_clean_provider_is_top_tier():
    book = parse_all_providers(_load("fe_all_providers.json")["data"])
    # Moonshot: no train, no retention, discloses a domestic datacenter → perfect.
    g = custody_score(book.lookup(slug="moonshotai"))
    assert g.grade == "S" and g.score == 100
    assert g.is_top and g.notch_count == 0
    assert not g.penalties


def test_custody_anthropic_is_graded_with_rap_sheet():
    book = parse_all_providers(_load("fe_all_providers.json")["data"])
    g = custody_score(book.lookup(slug="anthropic"))
    # retains 30d (-6) + requires user ids (-8) + datacenter undisclosed (-4) = 82.
    assert g.score == 82 and g.grade == "B"
    assert g.notch_count == 1            # only the retention is an active "offense"
    labels = [p.label for p in g.penalties]
    assert any("Retains prompts 30 days" in l for l in labels)


def test_training_is_a_hard_cap_to_F():
    """The cardinal sin: ANY provider that trains on prompts is floored at F,
    score ≤ 39, no matter how clean the rest of its record."""
    book = parse_all_providers(_load("fe_all_providers.json")["data"])
    trainers = [p for p in book.all() if p.trains]
    assert len(trainers) >= 2            # fixture has DeepSeek, Nvidia, Stealth…
    for p in trainers:
        g = custody_score(p)
        assert g.grade == "F" and g.score <= 39


def test_hard_cap_flag_set_when_it_actually_lowers_score():
    # Nvidia trains but is otherwise clean (would be ~47) → capped to 39.
    g = custody_score(_provider(trains=True))
    assert g.capped is True and g.score == 39 and g.grade == "F"


def test_notch_count_equals_offenses_capped_at_four():
    g = custody_score(_provider(
        trains=True, can_publish=True, retains=True, retention_days=90,
        datacenters=("SG",), datacenters_known=True, headquarters="US"))
    # offenses: trains, publishes, retains-with-days, cross-border = 4
    assert g.notch_count == 4
    assert g.notch_count == min(4, len(g.offenses))


def test_score_is_clamped_and_grade_covers_full_range():
    worst = custody_score(_provider(
        trains=True, can_publish=True, trains_openrouter=True, retains=True,
        retention_days=365, requires_user_ids=True, headquarters="CN"))
    assert 0 <= worst.score <= 100
    best = custody_score(_provider(datacenters=("US",), datacenters_known=True,
                                   headquarters="US"))
    assert best.score == 100 and best.grade == "S"


def test_custody_null_safety_on_empty_provider():
    g = custody_score(ProviderTrust())   # all defaults, no policy
    assert isinstance(g, CustodyGrade)
    assert 0 <= g.score <= 100
    assert g.grade in {t[0] for t in TRUST_TIERS}


def test_custody_positives_listed_for_clean_behaviors():
    g = custody_score(_provider(datacenters=("US",), datacenters_known=True,
                                headquarters="US"))
    assert "Never trains on your prompts" in g.positives
    assert "Zero prompt retention" in g.positives


def test_penalties_sorted_worst_first():
    g = custody_score(_provider(trains=True, requires_user_ids=True))
    deltas = [p.delta for p in g.penalties]
    assert deltas == sorted(deltas)      # most-negative first


# ---------------------------------------------------------------------------
#  Speed board  (rankings/performance)
# ---------------------------------------------------------------------------
def test_performance_parses_and_keys_by_permaslug():
    sb = parse_performance(_load("fe_rankings_performance.json")["data"])
    opus = sb.lookup("anthropic/claude-4.8-opus-20260528")
    assert opus is not None
    assert opus.p50_throughput == 54.0
    assert opus.best_latency_provider  # carried through


def test_throughput_percentile_against_the_field():
    sb = parse_performance(_load("fe_rankings_performance.json")["data"])
    # Opus throughput (54 t/s) beats 3 of the other 8 ranked models → 0.375.
    assert sb.throughput_percentile("anthropic/claude-4.8-opus-20260528") == pytest.approx(0.375)


def test_latency_percentile_lower_is_faster():
    sb = parse_performance(_load("fe_rankings_performance.json")["data"])
    # Opus p50 latency (1703ms) is lower than only 1 of the other 8 → 0.125.
    assert sb.latency_percentile("anthropic/claude-4.8-opus-20260528") == pytest.approx(0.125)


def test_percentile_unknown_model_is_none():
    sb = parse_performance(_load("fe_rankings_performance.json")["data"])
    assert sb.throughput_percentile("nobody/nope") is None


def test_percentile_field_of_one_is_none():
    sb = parse_performance([
        {"slug": "only/one", "name": "One", "p50_throughput": 100, "p50_latency": 50},
    ])
    assert sb.throughput_percentile("only/one") is None


# ---------------------------------------------------------------------------
#  Endpoint refs + uptime history
# ---------------------------------------------------------------------------
def test_endpoint_refs_extract_uuid_and_provider_slug():
    refs = parse_endpoint_refs(_load("fe_stats_endpoint.json")["data"])
    assert len(refs) == 6
    by_slug = {r.provider_slug: r for r in refs}
    assert "anthropic" in by_slug
    # Each ref carries the UUID uptime-hourly needs.
    assert all(len(r.id) >= 8 for r in refs)


def test_endpoint_refs_skip_rows_without_id():
    refs = parse_endpoint_refs([{"provider_slug": "x"}, {"id": "u1", "provider_name": "X"}])
    assert [r.id for r in refs] == ["u1"]


def test_uptime_hourly_is_reversed_to_chronological():
    hist = parse_uptime_hourly(_load("fe_uptime_hourly.json"))
    assert len(hist) == 73
    # API returns newest-first; we normalize to oldest-first so a ribbon
    # paints left=old, right=now.
    assert hist.points[0][0] == "2026-06-21 00:00:00"
    assert hist.points[-1][0] == "2026-06-24 00:00:00"


def test_uptime_history_aggregates():
    hist = parse_uptime_hourly(_load("fe_uptime_hourly.json"))
    assert hist.latest == pytest.approx(100.0)
    assert hist.average == pytest.approx(99.869, abs=0.01)
    worst_date, worst_val = hist.worst
    assert worst_date == "2026-06-21 23:00:00"
    assert worst_val == pytest.approx(98.778, abs=0.01)
    assert hist.outage_hours == 1   # one hour below 99%


def test_uptime_hourly_handles_nulls_and_empty():
    hist = parse_uptime_hourly({"data": {"history": [
        {"date": "h2", "uptime": None}, {"date": "h1", "uptime": 100},
    ]}})
    assert hist.points == [("h1", 100.0), ("h2", None)]   # reversed
    assert hist.latest == 100.0          # skips the trailing None
    assert hist.average == 100.0
    assert parse_uptime_hourly({}).points == []
    assert parse_uptime_hourly({"data": {}}).worst is None
