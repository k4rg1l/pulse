"""Deterministic render tests for #8 THE FAULT LINE (price-drift seismograph).

Drives a headless (offscreen) QApplication and MEASURES the rendered crack — its
silent-degrade (quiet == zero pixels / zero hit rects / unchanged height), the
fault path's edge-channel geometry, the kink/amplitude scaling, the per-row
tremor ticks, the two-pole accent, the acknowledge → shimmer-drop, and pixel
samples in the amber/violet lane — rather than eyeballing (MEMORY: validation
must be deterministic). The pure diff/store policy lives in test_price_drift.py.
"""
import time

import pytest

from api_client import EndpointInfo, ModelEndpoints
from price_drift import (PriceSnapshotStore, PriceSnap, diff_snaps,
                         snapshot_endpoints, DriftResult, ADVERSE, FAVORABLE)


SEISMIC_AMBER = "#ff9e3d"
QUARTZ_VIOLET = "#b07cff"


def _ep(name, prompt, deranked=False):
    ep = EndpointInfo(provider_name=name, tag=name.lower(),
                      pricing_prompt=prompt, pricing_completion=prompt * 5,
                      latency_p50=900.0, uptime_last_30m=100.0,
                      throughput_p50=50.0)
    if deranked:
        ep.is_deranked = True
    return ep


def _card(eps, model_id="x/y", model_name="Y"):
    from widgets import PinnedModelCard
    card = PinnedModelCard(model_id)
    card.set_endpoints(ModelEndpoints(model_id=model_id, model_name=model_name,
                                      endpoints=eps))
    card.resize(560, card.height())
    return card


def _drift_from(baseline_snaps, eps, store_marks_fresh=True):
    """Build a DriftResult the way the dashboard would: diff a baseline against
    the live endpoints. `store_marks_fresh` mirrors the store's first-detection
    is_fresh=True."""
    r = diff_snaps(baseline_snaps, snapshot_endpoints(eps))
    r.is_fresh = store_marks_fresh
    return r


def _render(card):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    img = QImage(card.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    card.render(p, QPoint(0, 0))
    p.end()
    return img


def _snap(prompt, name="", deranked=False):
    return PriceSnap(prompt=prompt, completion=prompt * 5, is_deranked=deranked,
                     name=name, ts=time.time() - 3600)


# ---------------------------------------------------------------------------
# (1) QUIET == NOTHING — no baseline OR identical → drift None, no crack, no
#     hit rects, height unchanged (the silent-degrade contract).
# ---------------------------------------------------------------------------
def test_quiet_paints_nothing_and_adds_no_height(qapp):
    eps = [_ep("A", 1e-6)]
    card = _card(eps)
    h0 = card.height()
    card.set_drift(None)                         # quiet
    assert card.height() == h0                   # ZERO height (decision E)
    assert card.has_drift() is False
    _render(card)
    assert card._drift_hits == []                # no clickable targets
    assert card._drift_geom is None              # nothing measured


def test_magnitude_zero_result_is_treated_as_quiet(qapp):
    eps = [_ep("A", 1e-6)]
    card = _card(eps)
    h0 = card.height()
    card.set_drift(DriftResult(magnitude=0.0, direction=ADVERSE))
    assert card._drift is None                   # coerced to None
    assert card.height() == h0
    _render(card)
    assert card._drift_hits == []


def test_quiet_crack_pixels_match_background(qapp):
    """No drift → the left-edge channel is pure card background (no etched
    crack). Sample the channel mid at the card's vertical centre."""
    from theme import Colors
    card = _card([_ep("A", 1e-6)])
    card.set_drift(None)
    img = _render(card)
    bg = Colors.BG_CARD
    cx = int((card.FAULT_X_MIN + card.FAULT_X_MAX) / 2)
    cy = int(card.height() / 2)
    px = img.pixelColor(cx, cy)
    assert (px.red(), px.green(), px.blue()) == (bg.red(), bg.green(), bg.blue())


# ---------------------------------------------------------------------------
# (2) PRICE-RISE tremor — stored $1.00 → $1.22 (per-Mtok) → mag in [0.40,0.48],
#     adverse, accent amber, ident in moved_rows, a tick rect recorded.
# ---------------------------------------------------------------------------
def test_price_rise_tremor(qapp):
    eps = [_ep("DeepInfra", 1.22e-6)]            # current $1.22/Mtok
    base = {"deepinfra": _snap(1.00e-6, name="DeepInfra")}
    card = _card(eps)
    r = _drift_from(base, eps)
    card.set_drift(r)
    assert 0.40 <= card._drift.magnitude <= 0.48
    assert card._drift.direction == ADVERSE
    assert card.drift_accent() == SEISMIC_AMBER
    assert "deepinfra" in card._drift.moved_rows
    _render(card)
    # a tremor-tick rect was recorded for the moved row
    tick_idents = [ident for _r, ident in card._drift_hits if ident is not None]
    assert "deepinfra" in tick_idents
    assert "deepinfra" in card._drift_geom["ticks"]


def test_price_rise_paints_amber_pixels_on_the_crack(qapp):
    """The crack is not blank: it tints the edge channel in the amber lane."""
    eps = [_ep("DeepInfra", 1.5e-6)]
    base = {"deepinfra": _snap(1.0e-6, name="DeepInfra")}
    card = _card(eps)
    card.set_drift(_drift_from(base, eps))
    img = _render(card)
    # scan the whole crack channel column band for an amber-ish pixel
    found = False
    for x in range(0, int(card.FAULT_X_MAX) + 2):
        for y in range(10, card.height() - 10):
            px = img.pixelColor(x, y)
            if px.alpha() > 0 and px.red() > 180 and 90 < px.green() < 200 and px.blue() < 130:
                found = True
                break
        if found:
            break
    assert found, "no seismic-amber crack pixel found in the edge channel"


# ---------------------------------------------------------------------------
# (3) DERANK (synthetic) → mag ≥ 0.6 adverse.
# ---------------------------------------------------------------------------
def test_derank_synthetic(qapp):
    eps = [_ep("Fireworks", 1e-6, deranked=True)]
    base = {"fireworks": _snap(1e-6, name="Fireworks", deranked=False)}
    card = _card(eps)
    card.set_drift(_drift_from(base, eps))
    assert card._drift.magnitude >= 0.6
    assert card._drift.direction == ADVERSE
    assert card.drift_accent() == SEISMIC_AMBER
    assert "fireworks" in card._drift.moved_rows


# ---------------------------------------------------------------------------
# (4) CHEAPER-APPEARED → favorable, accent violet.
# ---------------------------------------------------------------------------
def test_cheaper_appeared_is_violet(qapp):
    eps = [_ep("Pricey", 2e-6), _ep("Bargain", 1e-6)]
    base = {"pricey": _snap(2e-6, name="Pricey")}   # Bargain is NEW + undercuts
    card = _card(eps)
    card.set_drift(_drift_from(base, eps))
    assert card._drift.direction == FAVORABLE
    assert card.drift_accent() == QUARTZ_VIOLET
    assert "bargain" in card._drift.moved_rows


def test_cheaper_appeared_paints_violet_pixels(qapp):
    eps = [_ep("Pricey", 2e-6), _ep("Bargain", 1e-6)]
    base = {"pricey": _snap(2e-6, name="Pricey")}
    card = _card(eps)
    card.set_drift(_drift_from(base, eps))
    img = _render(card)
    found = False
    for x in range(0, int(card.FAULT_X_MAX) + 2):
        for y in range(10, card.height() - 10):
            px = img.pixelColor(x, y)
            # quartz violet ~ (176,124,255): blue dominant, red mid-high
            if px.alpha() > 0 and px.blue() > 180 and px.red() > 120 and px.green() < 170:
                found = True
                break
        if found:
            break
    assert found, "no quartz-violet crack pixel found in the edge channel"


# ---------------------------------------------------------------------------
# (5) NOISE GATE — a sub-1% move → drift None (no false tremor).
# ---------------------------------------------------------------------------
def test_noise_gate_sub_one_percent(qapp):
    eps = [_ep("A", 1.005e-6)]                    # +0.5% < gate
    base = {"a": _snap(1.0e-6, name="A")}
    card = _card(eps)
    r = diff_snaps(base, snapshot_endpoints(eps))
    assert r.quiet is True
    # the dashboard would push None for a quiet diff
    card.set_drift(None if r.quiet else r)
    assert card.has_drift() is False
    _render(card)
    assert card._drift_hits == []


# ---------------------------------------------------------------------------
# (6) GEOMETRY — the fault path bbox x in [2,9], never intersects the seal box
#     (x >= PAD_X) or the band emblem column (cx = 21).
# ---------------------------------------------------------------------------
def test_fault_path_lives_in_the_edge_channel(qapp):
    eps = [_ep("A", 1.5e-6)]
    base = {"a": _snap(1.0e-6, name="A")}
    card = _card(eps)
    card.set_drift(_drift_from(base, eps))
    _render(card)
    bbox = card._drift_geom["bbox"]
    assert bbox.left() >= card.FAULT_X_MIN - 0.01
    assert bbox.right() <= card.FAULT_X_MAX + 0.01
    # never touches the seal column (x >= PAD_X) or the emblem center (cx=21)
    assert bbox.right() < card.PAD_X
    assert bbox.right() < card._icon_col_cx()
    # vertical span stays inside the rounded corners
    assert bbox.top() >= card.FAULT_Y_MARGIN - 0.01
    assert bbox.bottom() <= card.height() - card.FAULT_Y_MARGIN + 0.01


def test_best_row_tick_nudges_inboard(qapp):
    """On a best row the tremor tick steps from PAD_X-4 to PAD_X-2 to clear the
    gold accent (decision F / the real-estate-map nudge)."""
    eps = [_ep("A", 1.5e-6)]                       # single ep → it's the best row
    base = {"a": _snap(1.0e-6, name="A")}
    card = _card(eps)
    assert card._best is not None                  # A is best
    card.set_drift(_drift_from(base, eps))
    _render(card)
    rect, _c = card._drift_geom["ticks"]["a"]
    assert rect.left() == pytest.approx(card.PAD_X - 2)


def test_non_best_row_tick_at_pad_minus_4(qapp):
    # two providers: A is best (lower latency), B is not; move B's price.
    a = _ep("A", 1.0e-6); a.latency_p50 = 100.0
    b = _ep("B", 1.5e-6); b.latency_p50 = 2000.0
    eps = [a, b]
    base = {"a": _snap(1.0e-6, name="A"), "b": _snap(1.0e-6, name="B")}
    card = _card(eps)
    card.set_drift(_drift_from(base, eps))
    _render(card)
    assert "b" in card._drift_geom["ticks"]
    rect, _c = card._drift_geom["ticks"]["b"]
    assert rect.left() == pytest.approx(card.PAD_X - 4)


# ---------------------------------------------------------------------------
# (7) KINK/AMP SCALING (decision F) — mag 1.0 → 9 kinks amp 7; mag ~0.1 → 3
#     kinks amp ~2.5.
# ---------------------------------------------------------------------------
def test_kink_amp_scaling_max(qapp):
    eps = [_ep("A", 2e-6)]                          # doubling → mag clamps to 1.0
    base = {"a": _snap(1.0e-6, name="A")}
    card = _card(eps)
    r = _drift_from(base, eps)
    assert r.magnitude == pytest.approx(1.0)
    card.set_drift(r)
    _render(card)
    assert card._drift_geom["kinks"] == 9
    assert card._drift_geom["amp"] == pytest.approx(7.0)


def test_kink_amp_scaling_small(qapp):
    # a small but above-gate move: +5% → mag = 2*0.05 = 0.10
    eps = [_ep("A", 1.05e-6)]
    base = {"a": _snap(1.0e-6, name="A")}
    card = _card(eps)
    r = _drift_from(base, eps)
    assert r.magnitude == pytest.approx(0.10, abs=1e-3)
    card.set_drift(r)
    _render(card)
    # kinks = clamp(3 + round(0.10*6), 3, 9) = clamp(3+1,..) = 4 ... round(0.6)=1
    assert card._drift_geom["kinks"] == 3 + round(0.10 * 6)
    assert card._drift_geom["amp"] == pytest.approx(2.0 + 0.10 * 5.0, abs=1e-6)
    assert 2.0 <= card._drift_geom["amp"] <= 3.0


# ---------------------------------------------------------------------------
# (8) ACKNOWLEDGE → _drift_fresh False + _wants_shimmer drops (no other elite)
#     + the persisted baseline now equals current so a re-diff is quiet.
# ---------------------------------------------------------------------------
def test_acknowledge_drops_fresh_and_shimmer(qapp):
    eps = [_ep("A", 1.5e-6)]
    base = {"a": _snap(1.0e-6, name="A")}
    card = _card(eps)
    card.show()
    qapp.processEvents()
    card.set_drift(_drift_from(base, eps, store_marks_fresh=True))
    assert card._drift_fresh is True
    assert card._wants_shimmer() is True
    assert card._shimmer_timer.isActive()         # fresh drift breathes
    card.acknowledge()
    assert card._drift_fresh is False
    assert card._wants_shimmer() is False         # no other elite
    qapp.processEvents()
    assert not card._shimmer_timer.isActive()
    # crack still drawn (persists until a quiet re-diff)
    assert card.has_drift() is True
    card.hide()
    qapp.processEvents()


def test_acknowledge_persists_baseline_no_refire(isolate_appdata, qapp):
    """End-to-end through the STORE: a drift fires fresh, acknowledge writes the
    baseline to disk, and the next observe() (same endpoints) is quiet — the
    crack would clear and the SAME drift never re-fires (the durable half)."""
    import price_drift as pd
    store = PriceSnapshotStore()
    store.observe("x/y", [_ep("A", 1.0e-6)])      # baseline
    r1 = store.observe("x/y", [_ep("A", 1.5e-6)])  # drift, fresh
    assert r1 is not None and r1.is_fresh
    store.acknowledge("x/y", [_ep("A", 1.5e-6)])
    store.save()
    # reload from disk → the acknowledged baseline survived
    reloaded = PriceSnapshotStore.load()
    r2 = reloaded.observe("x/y", [_ep("A", 1.5e-6)])
    assert r2 is None                              # quiet → crack clears, no re-fire


# ---------------------------------------------------------------------------
# Dossier
# ---------------------------------------------------------------------------
def test_drift_dossier_reads_out_tremors(qapp):
    eps = [_ep("DeepInfra", 1.22e-6)]
    base = {"deepinfra": _snap(1.0e-6, name="DeepInfra")}
    card = _card(eps)
    card.set_drift(_drift_from(base, eps))
    h = card.drift_html()
    assert "SEISMOGRAPH" in h
    assert "DeepInfra" in h
    assert "+22%" in h
    assert "Last quiet reading" in h


def test_drift_dossier_empty_when_quiet(qapp):
    card = _card([_ep("A", 1e-6)])
    card.set_drift(None)
    assert card.drift_html() == ""
    # accent has a safe default even with no drift
    assert card.drift_accent() == SEISMIC_AMBER


def test_show_drift_gate_removes_crack(qapp):
    eps = [_ep("A", 1.5e-6)]
    base = {"a": _snap(1.0e-6, name="A")}
    card = _card(eps)
    card.set_drift(_drift_from(base, eps))
    assert card.has_drift() is True
    card.set_show_drift(False)
    assert card.has_drift() is False              # gated off → nothing
    _render(card)
    assert card._drift_hits == []


# ---------------------------------------------------------------------------
# Click
# ---------------------------------------------------------------------------
class _Press:
    def __init__(self, pos):
        from PySide6.QtCore import Qt
        self._pos, self._btn = pos, Qt.MouseButton.LeftButton
    def button(self):
        return self._btn
    def position(self):
        return self._pos


def test_drift_click_emits_with_model_id(qapp):
    from PySide6.QtCore import QPointF
    eps = [_ep("A", 1.5e-6)]
    base = {"a": _snap(1.0e-6, name="A")}
    card = _card(eps, model_id="anthropic/claude-opus-4.8")
    card.set_drift(_drift_from(base, eps))
    _render(card)
    seen = []
    card.drift_clicked.connect(lambda mid, pos: seen.append(mid))
    # click the left-edge band (x in [0, PAD_X-2])
    card.mousePressEvent(_Press(QPointF(3, card.height() / 2)))
    assert seen == ["anthropic/claude-opus-4.8"]
