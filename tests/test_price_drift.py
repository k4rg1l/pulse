"""Pure-engine tests for #8 THE FAULT LINE (price-drift watcher).

No Qt here — this proves the magnitude formula, the noise gate, the structural
floors (derank / cheaper-appeared), the baseline-update POLICY (first-sight
silent, quiet-rolls-forward, drift-persists, acknowledge-no-refire), and the
json round-trip + prune. The widget render is proven in test_fault_line.py.

The orchestrator pinned the magnitude test bands (decision A); these are the
exact assertions.
"""
import json
import time
from pathlib import Path

import pytest

from api_client import EndpointInfo
import price_drift as pd
from price_drift import (PriceSnapshotStore, PriceSnap, diff_snaps,
                         snapshot_endpoints, price_magnitude,
                         ADVERSE, FAVORABLE, KIND_PRICE_UP, KIND_PRICE_DOWN,
                         KIND_CHEAPER, KIND_DERANK, STRUCTURAL_FLOOR)


def _ep(name, prompt, completion=None, deranked=False):
    ep = EndpointInfo(provider_name=name, tag=name.lower(),
                      pricing_prompt=prompt,
                      pricing_completion=completion if completion is not None else prompt * 5)
    # is_deranked is NOT a real EndpointInfo field (decision D); attach it
    # synthetically so the engine's getattr path is exercised.
    if deranked:
        ep.is_deranked = True
    return ep


def _snap(prompt, completion=0.0, deranked=False, name="", ts=None):
    return PriceSnap(prompt=prompt, completion=completion, is_deranked=deranked,
                     name=name, ts=ts if ts is not None else time.time())


# ---------------------------------------------------------------------------
# MAGNITUDE FORMULA (decision A) — pin the exact test bands.
# ---------------------------------------------------------------------------
def test_magnitude_22pct_rise_is_about_0_44():
    # 2 * 0.22 = 0.44
    assert price_magnitude(1.0, 1.22) == pytest.approx(0.44, abs=1e-9)


def test_magnitude_halving_clamps_to_1():
    assert price_magnitude(1.0, 0.50) == pytest.approx(1.0)


def test_magnitude_doubling_clamps_to_1():
    assert price_magnitude(1.0, 2.0) == pytest.approx(1.0)


def test_magnitude_old_zero_is_zero():
    assert price_magnitude(0.0, 5.0) == 0.0


# ---------------------------------------------------------------------------
# THE PURE DIFF
# ---------------------------------------------------------------------------
def test_price_rise_is_adverse_in_moved_rows():
    base = {"deepinfra": _snap(1e-6, name="DeepInfra")}
    cur = snapshot_endpoints([_ep("DeepInfra", 1.22e-6)])
    r = diff_snaps(base, cur)
    assert r.magnitude == pytest.approx(0.44, abs=1e-3)
    assert r.direction == ADVERSE
    assert "deepinfra" in r.moved_rows
    assert r.tremors[0].kind == KIND_PRICE_UP


def test_price_fall_is_favorable():
    base = {"together": _snap(1e-6, name="Together")}
    cur = snapshot_endpoints([_ep("Together", 0.80e-6)])  # -20%
    r = diff_snaps(base, cur)
    assert r.direction == FAVORABLE
    assert r.magnitude == pytest.approx(0.40, abs=1e-3)
    assert r.tremors[0].kind == KIND_PRICE_DOWN


def test_sub_one_percent_move_is_noise_gated():
    base = {"a": _snap(1.0e-6, name="A")}
    cur = snapshot_endpoints([_ep("A", 1.005e-6)])  # +0.5% < gate
    r = diff_snaps(base, cur)
    assert r.moved_rows == set()
    assert r.magnitude == 0.0
    assert r.quiet is True


def test_derank_flip_is_structural_adverse():
    base = {"fireworks": _snap(1e-6, deranked=False, name="Fireworks")}
    cur = snapshot_endpoints([_ep("Fireworks", 1e-6, deranked=True)])
    r = diff_snaps(base, cur)
    assert r.magnitude >= STRUCTURAL_FLOOR
    assert r.direction == ADVERSE
    assert "fireworks" in r.moved_rows
    assert any(t.kind == KIND_DERANK for t in r.tremors)


def test_cheaper_provider_appeared_is_favorable():
    base = {"pricey": _snap(2e-6, name="Pricey")}
    # a NEW endpoint priced below the stored minimum (2e-6)
    cur = snapshot_endpoints([_ep("Pricey", 2e-6), _ep("Bargain", 1e-6)])
    r = diff_snaps(base, cur)
    assert r.direction == FAVORABLE
    assert r.magnitude >= STRUCTURAL_FLOOR
    assert "bargain" in r.moved_rows
    assert any(t.kind == KIND_CHEAPER for t in r.tremors)


def test_new_endpoint_not_cheaper_is_not_a_tremor():
    base = {"a": _snap(1e-6, name="A")}
    cur = snapshot_endpoints([_ep("A", 1e-6), _ep("B", 5e-6)])  # B pricier
    r = diff_snaps(base, cur)
    assert r.quiet is True
    assert "b" not in r.moved_rows


def test_net_direction_dominant_pole_and_tie_goes_adverse():
    # one provider rises 50% (adverse mag 1.0), another falls 10% (favorable 0.2)
    base = {"up": _snap(1e-6, name="Up"), "down": _snap(1e-6, name="Down")}
    cur = snapshot_endpoints([_ep("Up", 1.5e-6), _ep("Down", 0.9e-6)])
    r = diff_snaps(base, cur)
    assert r.direction == ADVERSE
    assert {"up", "down"} <= r.moved_rows
    # tie -> adverse: equal-magnitude opposing moves
    base2 = {"u": _snap(1e-6, name="U"), "d": _snap(1e-6, name="D")}
    cur2 = snapshot_endpoints([_ep("U", 1.2e-6), _ep("D", 0.8e-6)])  # both 0.4
    r2 = diff_snaps(base2, cur2)
    assert r2.direction == ADVERSE


def test_tremors_sorted_largest_first():
    base = {"big": _snap(1e-6, name="Big"), "small": _snap(1e-6, name="Small")}
    cur = snapshot_endpoints([_ep("Big", 1.5e-6), _ep("Small", 1.1e-6)])
    r = diff_snaps(base, cur)
    assert r.tremors[0].ident == "big"
    assert r.tremors[0].magnitude >= r.tremors[1].magnitude


# ---------------------------------------------------------------------------
# THE BASELINE-UPDATE POLICY (decision C) — the subtle core.
# ---------------------------------------------------------------------------
def test_first_sight_stores_and_stays_silent():
    store = PriceSnapshotStore()
    r = store.observe("m/x", [_ep("A", 1e-6)])
    assert r is None                              # silent, no phantom quake
    assert store.baseline_for("m/x")["a"].prompt == pytest.approx(1e-6)


def test_quiet_rolls_baseline_forward_and_returns_none():
    store = PriceSnapshotStore()
    store.observe("m/x", [_ep("A", 1e-6)])        # first sight
    r = store.observe("m/x", [_ep("A", 1e-6)])    # identical -> quiet
    assert r is None
    # a sub-gate change is also quiet but rolls forward to the new value
    r2 = store.observe("m/x", [_ep("A", 1.004e-6)])
    assert r2 is None
    assert store.baseline_for("m/x")["a"].prompt == pytest.approx(1.004e-6)


def test_drift_is_fresh_first_then_persists_not_fresh():
    store = PriceSnapshotStore()
    store.observe("m/x", [_ep("A", 1e-6)])        # baseline
    r1 = store.observe("m/x", [_ep("A", 1.22e-6)])
    assert r1 is not None and r1.is_fresh is True
    # baseline NOT overwritten -> the SAME drift persists, now not fresh
    r2 = store.observe("m/x", [_ep("A", 1.22e-6)])
    assert r2 is not None and r2.is_fresh is False
    assert r2.magnitude == pytest.approx(0.44, abs=1e-3)
    # baseline still the ORIGINAL (crack persists)
    assert store.baseline_for("m/x")["a"].prompt == pytest.approx(1e-6)


def test_acknowledge_writes_baseline_so_next_diff_is_quiet_no_refire():
    store = PriceSnapshotStore()
    store.observe("m/x", [_ep("A", 1e-6)])
    cur = [_ep("A", 1.22e-6)]
    r1 = store.observe("m/x", cur)
    assert r1 is not None and r1.is_fresh
    # dossier opens -> acknowledge writes current as the new baseline
    store.acknowledge("m/x", cur)
    assert store.is_fresh("m/x") is False
    # the next refresh (same current) now diffs current-vs-current -> quiet
    r2 = store.observe("m/x", cur)
    assert r2 is None                              # crack clears, no re-fire
    assert store.baseline_for("m/x")["a"].prompt == pytest.approx(1.22e-6)


def test_acknowledge_then_a_brand_new_move_fires_fresh_again():
    store = PriceSnapshotStore()
    store.observe("m/x", [_ep("A", 1e-6)])
    store.observe("m/x", [_ep("A", 1.22e-6)])
    store.acknowledge("m/x", [_ep("A", 1.22e-6)])
    store.observe("m/x", [_ep("A", 1.22e-6)])      # quiet roll
    r = store.observe("m/x", [_ep("A", 1.5e-6)])    # a NEW move
    assert r is not None and r.is_fresh is True


# ---------------------------------------------------------------------------
# PERSISTENCE — json round-trip (BOM tolerant) + prune.
# ---------------------------------------------------------------------------
def test_round_trip_through_json(isolate_appdata):
    store = PriceSnapshotStore()
    store.observe("m/x", [_ep("A", 1e-6), _ep("B", 2e-6)])
    store.observe("m/x", [_ep("A", 1.22e-6), _ep("B", 2e-6)])  # drift, fresh
    store.save()
    assert pd.price_snaps_path().exists()
    loaded = PriceSnapshotStore.load()
    assert loaded.baseline_for("m/x")["a"].prompt == pytest.approx(1e-6)
    assert loaded.is_fresh("m/x") is True


def test_load_tolerates_bom(isolate_appdata):
    store = PriceSnapshotStore()
    store.observe("m/x", [_ep("A", 1e-6)])
    store.save()
    # rewrite WITH a BOM, as PowerShell's Set-Content -Encoding utf8 would
    p = pd.price_snaps_path()
    raw = p.read_text(encoding="utf-8")
    p.write_text("﻿" + raw, encoding="utf-8")
    loaded = PriceSnapshotStore.load()
    assert loaded.baseline_for("m/x")["a"].prompt == pytest.approx(1e-6)


def test_corrupt_file_starts_fresh(isolate_appdata):
    p = pd.price_snaps_path()
    p.write_text("{not valid json", encoding="utf-8")
    loaded = PriceSnapshotStore.load()        # must not raise
    assert loaded.baselines == {}


def test_prune_caps_models(isolate_appdata, monkeypatch):
    monkeypatch.setattr(pd, "MAX_MODELS", 3)
    store = PriceSnapshotStore()
    base = time.time()
    for i in range(6):
        # stagger ts so prune keeps the most-recent 3
        store.baselines[f"m/{i}"] = {"a": _snap(1e-6, ts=base + i)}
    store.save()                              # triggers _prune
    assert len(store.baselines) == 3
    assert set(store.baselines) == {"m/3", "m/4", "m/5"}


def test_prune_drops_stale_models(isolate_appdata):
    store = PriceSnapshotStore()
    old_ts = time.time() - pd.RETENTION_DAYS * 86400 - 10
    store.baselines["stale"] = {"a": _snap(1e-6, ts=old_ts)}
    store.baselines["live"] = {"a": _snap(1e-6, ts=time.time())}
    store.save()
    assert "stale" not in store.baselines
    assert "live" in store.baselines
