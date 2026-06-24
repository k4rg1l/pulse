"""Deterministic render tests for THE PULSE (#3 — the 73h uptime cardiogram).

Drives a headless (offscreen) QApplication and MEASURES the rendered cardiogram
— its no-height-change promise, the calm-green clean pulse, the depth-encoded red
gouge at the EXACT worst hour, the no-data gap, the legacy fallback, the click hit
rects, the dossier strip, and the flawless-heartbeat gating — rather than eyeballing
a screenshot (per MEMORY: validation must be deterministic). The pure parser logic
is covered in test_frontend_client.py; this proves the widget wiring + geometry.
"""
import json
from pathlib import Path

import pytest

from api_client import EndpointInfo, ModelEndpoints
from frontend_client import parse_uptime_hourly, UptimeHistory

FIX = Path(__file__).parent / "fixtures"

# The OUTAGE fixture's worst hour sits at this chronological index (oldest=0).
OUTAGE_WORST_I = 57
OUTAGE_WORST_DATE = "2026-06-23 15:00:00"


def _clean_hist():
    return parse_uptime_hourly(json.loads(
        (FIX / "fe_uptime_hourly.json").read_text(encoding="utf-8")))


def _outage_hist():
    return parse_uptime_hourly(json.loads(
        (FIX / "fe_uptime_hourly_outage.json").read_text(encoding="utf-8")))


def _flawless_hist(n=73):
    """A synthetic flawless window: 73 hours all >=99% (a couple of 100.0s),
    outage_hours == 0. The existing CLEAN fixture has one 98.78% notch, so it is
    NOT flawless — this is what the 'earned heartbeat' + clean-pulse path needs."""
    pts = []
    for i in range(n):
        d = f"2026-06-2{1 + i // 24} {i % 24:02d}:00:00"
        pts.append((d, 100.0 if i % 5 else 99.6))
    return UptimeHistory(points=pts)


def _gap_hist():
    """Synthetic: otherwise-healthy with ONE interior no-data (None) hour — the
    gap path (a polyline break, NOT a scar). Live data showed 0 Nones across the
    captured endpoints, so this path must be tested synthetically."""
    pts = []
    for i in range(20):
        d = f"2026-06-23 {i:02d}:00:00"
        v = None if i == 10 else 100.0
        pts.append((d, v))
    return UptimeHistory(points=pts)


def _ep(name="Anthropic", tag="anthropic"):
    return EndpointInfo(provider_name=name, tag=tag, latency_p50=900.0,
                        uptime_last_30m=100.0, throughput_p50=50.0,
                        pricing_prompt=1e-6, pricing_completion=5e-6)


def _card(hist, ident="anthropic", model_id="anthropic/claude-opus-4.8",
          model_name="Claude Opus 4.8"):
    from widgets import PinnedModelCard
    card = PinnedModelCard(model_id)
    card.set_endpoints(ModelEndpoints(model_id=model_id, model_name=model_name,
                                      endpoints=[_ep(tag=ident)]))
    if hist is not None:
        card.set_uptime({ident: hist})
    card.resize(560, card.height())
    return card


def _render(card):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    img = QImage(card.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    card.render(p, QPoint(0, 0))
    p.end()
    return img


# ---------------------------------------------------------------------------
# 1. NO HEIGHT CHANGE — the row glyph adds NO height (distinguishes from a band).
def test_pulse_adds_no_height(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("anthropic/claude-opus-4.8")
    card.set_endpoints(ModelEndpoints(model_id="anthropic/claude-opus-4.8",
                                      model_name="Claude Opus 4.8",
                                      endpoints=[_ep()]))
    h0 = card.height()
    card.set_uptime({"anthropic": _outage_hist()})
    assert card.height() == h0            # in-column glyph: zero reflow
    card.set_uptime({})                   # dropping it must also not perturb height
    assert card.height() == h0


# 2. CLEAN/FLAWLESS → calm green pulse, no scars.
def test_flawless_is_calm_green_no_scars(qapp):
    from theme import Colors
    card = _card(_flawless_hist())
    img = _render(card)
    assert card._pulse_has_outage is False
    assert card._pulse_dip_xs == []                       # zero scar ticks
    # worst point never plunges below baseline on a flawless record.
    assert card._pulse_worst_pt is not None
    assert card._pulse_worst_pt.y() <= card._pulse_baseline_y + 0.5
    # a pixel on the trace near baseline is green-ish (G channel dominant).
    bx = int(card._pulse_glyph_rect.left() + 3)
    by = int(card._pulse_baseline_y)
    found_green = False
    for dy in range(-3, 4):
        for dx in range(0, 30):
            px = img.pixelColor(bx + dx, by + dy)
            if px.alpha() > 0 and px.green() > px.red() and px.green() > px.blue():
                found_green = True
                break
        if found_green:
            break
    assert found_green, "expected a green-ish trace pixel on a flawless pulse"


# 3. OUTAGE → unmissable red gouge at the EXACT hour (depth is encoded).
def test_outage_gouges_red_below_baseline(qapp):
    card = _card(_outage_hist())
    img = _render(card)
    hist = _outage_hist()
    assert card._pulse_has_outage is True
    # one scar per <99 hour.
    assert len(card._pulse_dip_xs) == hist.outage_hours
    # THE core proof: the worst hour plunges BELOW the baseline.
    assert card._pulse_worst_pt is not None
    assert card._pulse_worst_pt.y() > card._pulse_baseline_y
    # and a RED-dominant pixel actually painted at the worst point.
    wx, wy = int(card._pulse_worst_pt.x()), int(card._pulse_worst_pt.y())
    red_here = False
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            px = img.pixelColor(wx + dx, wy + dy)
            if px.alpha() > 0 and px.red() > 150 and px.green() < 120 and px.blue() < 120:
                red_here = True
                break
        if red_here:
            break
    assert red_here, "expected a red-dominant pixel at the worst hour"


# 4. EXACT-HOUR MAPPING — the gouge sits at the real data→pixel hour.
def test_worst_hour_maps_to_exact_pixel(qapp):
    card = _card(_outage_hist())
    _render(card)
    g = card._pulse_glyph_rect
    inner_left = g.left() + 1.0
    inner_w = (g.right() - 1.0) - inner_left
    n = 73
    expected = inner_left + OUTAGE_WORST_I * (inner_w / (n - 1))
    assert card._pulse_worst_pt.x() == pytest.approx(expected, abs=0.6)


# 5. NO-DATA != OUTAGE — an interior None makes a polyline break, not a scar.
def test_interior_none_is_a_gap_not_a_scar(qapp):
    card = _card(_gap_hist())
    _render(card)
    # The cached segment list for this row splits into exactly 2 runs.
    seg_count = len(card._pulse_cache["anthropic"]["segments"])
    assert seg_count == 2
    # A gap contributes NO scar tick (it's healthy on both sides).
    assert card._pulse_dip_xs == []
    assert card._pulse_has_outage is False


# 6. FALLBACK — no uptime data → the legacy %-chip path still renders.
def test_fallback_to_legacy_chip(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y",
                                      endpoints=[_ep()]))
    h0 = card.height()
    card.resize(560, card.height())
    _render(card)                          # must not raise
    assert card.has_uptime() is False
    assert card._pulse_hits == []          # no cardiogram → no hit target
    assert card.height() == h0


def test_too_few_points_falls_back(qapp):
    # A history with a single observed point cannot draw a cardiogram.
    hist = UptimeHistory(points=[("2026-06-23 00:00:00", 100.0)])
    card = _card(hist)
    _render(card)
    assert card._pulse_hits == []          # fell back to the chip
    assert "anthropic" in card._uptime     # data present, just not drawable


# 7. ACCENT — verdict/border color matches the data.
def test_uptime_accent_matches_health(qapp):
    clean = _card(_flawless_hist())
    assert clean.uptime_accent("anthropic") == "#2ed573"
    out = _card(_outage_hist())
    assert out.uptime_accent("anthropic") == "#ff4757"     # worst 31% < 95


# 8. CLICK WIRING — hit rect equals the UPTIME column rect, and click fires.
class _Press:
    def __init__(self, pos):
        from PySide6.QtCore import Qt
        self._pos, self._btn = pos, Qt.MouseButton.LeftButton
    def button(self):
        return self._btn
    def position(self):
        return self._pos


def test_pulse_hit_rect_is_the_uptime_column(qapp):
    card = _card(_outage_hist())
    _render(card)
    assert len(card._pulse_hits) == 1
    rect, ident, _accent = card._pulse_hits[0]
    assert ident == "anthropic"
    # the hit rect is exactly the measured glyph rect (the UPTIME column).
    assert rect == card._pulse_glyph_rect
    assert rect.width() == card.UPTIME_W


def test_pulse_click_emits_with_ident(qapp):
    card = _card(_outage_hist())
    _render(card)
    seen = []
    card.uptime_clicked.connect(lambda mid, ident, pos: seen.append((mid, ident)))
    rect, ident, _ = card._pulse_hits[0]
    card.mousePressEvent(_Press(rect.center()))
    assert seen == [("anthropic/claude-opus-4.8", "anthropic")]


def test_pulse_click_does_not_steal_seal_clicks(qapp):
    """The pulse hit-test is last; a press in the seal column must NOT fire
    uptime_clicked (the columns don't overlap, but lock it)."""
    card = _card(_outage_hist())
    _render(card)
    from PySide6.QtCore import QPointF
    seen = []
    card.uptime_clicked.connect(lambda *a: seen.append(a))
    # a point in the far-left seal column, never in the right-side uptime column.
    card.mousePressEvent(_Press(QPointF(card.PAD_X + 1, card.height() - 11)))
    assert seen == []


# 9. DOSSIER STRIP — 73 bars, crimson worst bar; clean history yields no crimson.
def test_dossier_strip_has_73_bars_and_crimson_worst(qapp):
    from widgets import UptimeStripWidget
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    strip = UptimeStripWidget(_outage_hist())
    img = QImage(strip.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    strip.render(p, QPoint(0, 0))
    p.end()
    assert len(strip._strip_bar_xs) == 73
    # worst bar x ≈ the mapped index.
    pad = strip.PAD
    inner_w = strip.STRIP_W - 2 * pad
    expected = pad + OUTAGE_WORST_I * (inner_w / 72)
    assert strip._strip_worst_x == pytest.approx(expected, abs=1.0)
    # the worst bar is crimson-dominant somewhere along its column.
    wx = int(strip._strip_worst_x)
    crimson = False
    for y in range(strip.STRIP_H):
        px = img.pixelColor(wx, y)
        if px.alpha() > 0 and px.red() > 160 and px.green() < 120 and px.blue() < 110:
            crimson = True
            break
    assert crimson, "expected a crimson worst bar in the dossier strip"


def test_dossier_strip_clean_has_no_crimson(qapp):
    """A flawless history's strip is all green→ no crimson bars (the dossier
    encodes depth too, it is not a binary heat grid)."""
    from widgets import UptimeStripWidget
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    strip = UptimeStripWidget(_flawless_hist())
    img = QImage(strip.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    strip.render(p, QPoint(0, 0))
    p.end()
    crimson_pixels = 0
    for x in range(strip.STRIP_W):
        for y in range(0, strip.STRIP_H, 2):
            px = img.pixelColor(x, y)
            if px.alpha() > 0 and px.red() > 180 and px.green() < 100 and px.blue() < 90:
                crimson_pixels += 1
    assert crimson_pixels == 0


def test_uptime_html_renders_strip_and_verdict(qapp):
    out = _card(_outage_hist())
    h = out.uptime_html("anthropic")
    assert "data:image/png;base64," in h            # the painted strip embedded
    assert "OUTAGE HOUR" in h                         # the red verdict
    assert "DEEPEST DIP" in h                         # the callout
    assert "Longest Clean Streak" in h
    clean = _card(_flawless_hist())
    hc = clean.uptime_html("anthropic")
    assert "FLAWLESS" in hc                           # the earned banner
    # empty/absent → empty html (clean degrade).
    empty = _card(None)
    assert empty.uptime_html("anthropic") == ""


# 10. FLAWLESS HEARTBEAT GATING — only a flawless row earns the heartbeat.
def test_flawless_drives_heartbeat_outage_does_not(qapp):
    card = _card(_flawless_hist())
    assert card._uptime_alive is True
    card.show()
    qapp.processEvents()
    assert card._wants_shimmer() is True
    assert card._shimmer_timer.isActive()
    card.hide()
    qapp.processEvents()
    assert not card._shimmer_timer.isActive()

    # an outage-only card never animates.
    out = _card(_outage_hist())
    assert out._uptime_alive is False
    out.show()
    qapp.processEvents()
    assert out._wants_shimmer() is False
    out.hide()


def test_near_clean_fixture_is_not_flawless(qapp):
    """The existing CLEAN fixture has one 98.78% notch → has_outage True, one
    shallow scar (not a floor-plunge), and does NOT earn the heartbeat."""
    card = _card(_clean_hist())
    _render(card)
    assert card._pulse_has_outage is True
    assert len(card._pulse_dip_xs) == 1                # the single shallow notch
    assert card._uptime_alive is False
    # the shallow notch is only slightly below baseline (not floored).
    floor_y = card._pulse_glyph_rect.top() + card.ROW_H - card.PULSE_FLOOR_MARGIN
    assert card._pulse_worst_pt.y() < floor_y - 4      # nowhere near the floor
