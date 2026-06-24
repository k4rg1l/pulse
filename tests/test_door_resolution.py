"""#5 THE THRESHOLD — the pure door-resolution layer (no Qt).

Covers the save% math, the deterministic GREEN-DOOR rule (cheaper AND strictly
higher throughput → green; cheaper-but-slower → amber), and EVERY no-op case
(best None, cheapest IS best, best price 0, save% rounds to 0). The render/
geometry side is proven in test_threshold_door.py.
"""
from api_client import (
    EndpointInfo, resolve_door, DOOR_AMBER, DOOR_EMERALD,
)


def _ep(name, prompt, tput=50.0, lat=900.0, up=100.0):
    return EndpointInfo(provider_name=name, tag=name.lower(),
                        pricing_prompt=prompt, throughput_p50=tput,
                        latency_p50=lat, uptime_last_30m=up)


# ---------------------------------------------------------------------------
def test_save_pct_math_5_to_4_is_20pct():
    """best=$5/Mtok, cheapest=$4/Mtok → SAVE 20%."""
    best = _ep("Expensive", 5e-6)
    cheap = _ep("Cheap", 4e-6)
    d = resolve_door([best, cheap], best)
    assert d is not None
    assert d.save_pct == 20
    assert d.cheaper_name == "Cheap"
    assert d.from_name == "Expensive"
    assert d.from_mtok == 5.0 and d.to_mtok == 4.0


def test_green_door_when_cheaper_and_faster():
    """Cheaper AND strictly higher throughput → green + emerald accent."""
    best = _ep("Best", 5e-6, tput=40.0)
    cheap = _ep("Cheap", 4e-6, tput=80.0)         # cheaper AND faster
    d = resolve_door([best, cheap], best)
    assert d.green is True
    assert d.accent == DOOR_EMERALD


def test_amber_door_when_cheaper_but_slower():
    """Cheaper but LOWER throughput → not green, stays brass-amber (honest)."""
    best = _ep("Best", 5e-6, tput=80.0)
    cheap = _ep("Cheap", 4e-6, tput=40.0)         # cheaper but slower
    d = resolve_door([best, cheap], best)
    assert d.green is False
    assert d.accent == DOOR_AMBER


def test_equal_throughput_is_not_green():
    """STRICTLY higher (decision A) — equal throughput does not open the green
    door."""
    best = _ep("Best", 5e-6, tput=50.0)
    cheap = _ep("Cheap", 4e-6, tput=50.0)
    d = resolve_door([best, cheap], best)
    assert d.green is False


def test_missing_cheaper_throughput_is_not_green():
    best = _ep("Best", 5e-6, tput=50.0)
    cheap = _ep("Cheap", 4e-6, tput=None)
    d = resolve_door([best, cheap], best)
    assert d.green is False


# ---- no-op cases (decision C) ----
def test_noop_when_best_is_none():
    assert resolve_door([_ep("A", 4e-6), _ep("B", 5e-6)], None) is None


def test_noop_when_cheapest_is_best():
    """best already the cheapest priced endpoint → no door."""
    best = _ep("Best", 3e-6)
    other = _ep("Other", 5e-6)
    assert resolve_door([best, other], best) is None


def test_noop_when_best_price_is_zero():
    """Free model → divide-by-zero guard → no door."""
    best = _ep("Free", 0.0)
    other = _ep("Other", 4e-6)
    assert resolve_door([best, other], best) is None


def test_noop_when_save_rounds_to_zero():
    """A sub-half-percent saving rounds to 0 → no door."""
    best = _ep("Best", 1.0000e-6)
    cheap = _ep("Cheap", 0.9996e-6)               # ~0.04% cheaper
    assert resolve_door([best, cheap], best) is None


def test_noop_when_no_priced_endpoint():
    best = _ep("Best", 5e-6)
    # the only OTHER endpoint is unpriced (0) → cheapest priced is best itself
    assert resolve_door([best, _ep("Zero", 0.0)], best) is None


def test_zero_priced_endpoints_are_ignored_as_destination():
    """A $0 endpoint is not a real 'cheaper' door — the cheapest PRICED one is."""
    best = _ep("Best", 5e-6)
    zero = _ep("Zero", 0.0)
    real_cheap = _ep("RealCheap", 4e-6)
    d = resolve_door([best, zero, real_cheap], best)
    assert d is not None
    assert d.cheaper_name == "RealCheap"


def test_latency_delta_pct_for_honesty_line():
    """The dossier honesty line input: cheaper door 14% slower to first token."""
    best = _ep("Best", 5e-6, lat=1000.0)
    cheap = _ep("Cheap", 4e-6, lat=1140.0)        # +14% latency
    d = resolve_door([best, cheap], best)
    assert d.latency_delta_pct == 14


def test_from_to_metrics_are_carried():
    best = _ep("Best", 5e-6, tput=40.0, lat=1000.0)
    cheap = _ep("Cheap", 4e-6, tput=80.0, lat=600.0)
    d = resolve_door([best, cheap], best)
    assert d.from_throughput == 40.0 and d.to_throughput == 80.0
    assert d.from_latency == 1000.0 and d.to_latency == 600.0
