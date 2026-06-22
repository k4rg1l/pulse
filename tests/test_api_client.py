"""Unit tests for api_client.py dataclasses — the derived properties and
best-provider selection that drive the gauge and the pinned-model cards.

Pure logic only; no HTTP. The HTTP methods are covered by E2E/manual
testing (see docs/TESTING.md) since they hit the live OpenRouter API.
"""
import math

import pytest

from api_client import EndpointInfo, KeyInfo, ModelEndpoints, ModelInfo


# ---------------------------------------------------------------------------
#  KeyInfo derived properties
# ---------------------------------------------------------------------------

def test_remaining_prefers_explicit_limit_remaining():
    k = KeyInfo(limit_remaining=7.5, total_credits=20.0, total_usage=5.0)
    assert k.remaining == pytest.approx(7.5)


def test_remaining_falls_back_to_credits_minus_usage():
    k = KeyInfo(total_credits=20.0, total_usage=5.0)
    assert k.remaining == pytest.approx(15.0)


def test_remaining_is_none_with_no_signal():
    assert KeyInfo().remaining is None


def test_credit_percent_from_credits():
    k = KeyInfo(total_credits=20.0, total_usage=5.0)  # 15 remaining of 20
    assert k.credit_percent == pytest.approx(0.75)


def test_credit_percent_defaults_to_full_when_unknown():
    assert KeyInfo().credit_percent == pytest.approx(1.0)


def test_burn_rate_hourly_is_daily_over_24():
    k = KeyInfo(usage_daily=24.0)
    assert k.burn_rate_hourly == pytest.approx(1.0)


def test_days_remaining_infinite_without_burn():
    k = KeyInfo(total_credits=20.0, total_usage=5.0, usage_daily=0.0)
    assert math.isinf(k.days_remaining)


def test_days_remaining_from_rate():
    k = KeyInfo(total_credits=20.0, total_usage=5.0, usage_daily=5.0)  # 15 / 5
    assert k.days_remaining == pytest.approx(3.0)


# ---------------------------------------------------------------------------
#  ModelInfo / EndpointInfo price conversions ($/token -> $/Mtok)
# ---------------------------------------------------------------------------

def test_model_price_per_mtok():
    m = ModelInfo(pricing_prompt=0.000003, pricing_completion=0.000015)
    assert m.price_per_mtok_prompt == pytest.approx(3.0)
    assert m.price_per_mtok_completion == pytest.approx(15.0)


def test_endpoint_price_per_mtok():
    e = EndpointInfo(pricing_prompt=0.0000005, pricing_completion=0.0000025)
    assert e.price_per_mtok_prompt == pytest.approx(0.5)
    assert e.price_per_mtok_completion == pytest.approx(2.5)


# ---------------------------------------------------------------------------
#  EndpointInfo.uptime fallback chain (30m -> 1d -> 5m)
# ---------------------------------------------------------------------------

def test_uptime_prefers_30m():
    e = EndpointInfo(uptime_last_30m=99.5, uptime_last_1d=98.0, uptime_last_5m=100.0)
    assert e.uptime == pytest.approx(99.5)


def test_uptime_falls_back_to_1d_then_5m():
    assert EndpointInfo(uptime_last_1d=97.0, uptime_last_5m=100.0).uptime == pytest.approx(97.0)
    assert EndpointInfo(uptime_last_5m=100.0).uptime == pytest.approx(100.0)
    assert EndpointInfo().uptime is None


def test_latency_aliases_map_to_p50():
    e = EndpointInfo(latency_p50=123.0, throughput_p50=45.0)
    assert e.latency_last_30m == pytest.approx(123.0)
    assert e.throughput_last_30m == pytest.approx(45.0)


# ---------------------------------------------------------------------------
#  ModelEndpoints.best_provider
# ---------------------------------------------------------------------------

def _ep(name, lat, up, prompt=0.0):
    return EndpointInfo(provider_name=name, latency_p50=lat, uptime_last_30m=up, pricing_prompt=prompt)


def test_best_provider_lowest_latency_among_high_uptime():
    me = ModelEndpoints(model_id="m", endpoints=[
        _ep("A", lat=100, up=99.5, prompt=0.001),
        _ep("B", lat=50, up=95.0, prompt=0.001),   # too low uptime -> excluded
        _ep("C", lat=80, up=100.0, prompt=0.002),
    ])
    best = me.best_provider()
    assert best.provider_name == "C"  # 80ms beats 100ms; B excluded


def test_best_provider_tie_breaks_on_cheaper_prompt():
    me = ModelEndpoints(model_id="m", endpoints=[
        _ep("C", lat=80, up=100.0, prompt=0.002),
        _ep("D", lat=80, up=99.0, prompt=0.001),  # same latency, cheaper
    ])
    assert me.best_provider().provider_name == "D"


def test_best_provider_includes_endpoints_with_unknown_uptime():
    me = ModelEndpoints(model_id="m", endpoints=[
        EndpointInfo(provider_name="E", latency_p50=70, uptime_last_30m=None),
    ])
    assert me.best_provider().provider_name == "E"


def test_best_provider_none_when_no_latency_data():
    me = ModelEndpoints(model_id="m", endpoints=[
        EndpointInfo(provider_name="F", latency_p50=None, uptime_last_30m=100.0),
    ])
    assert me.best_provider() is None


def test_best_provider_none_on_empty():
    assert ModelEndpoints(model_id="m", endpoints=[]).best_provider() is None
