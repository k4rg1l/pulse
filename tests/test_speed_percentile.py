"""Deterministic render tests for Speed Percentile (#4 — the velocity band).

Drives a headless (offscreen) QApplication and MEASURES the rendered band — its
hit-target, the meter knob geometry vs the percentile, painted pixels, and the
dossier HTML — rather than eyeballing a screenshot. The pure percentile/standing
logic is covered in test_frontend_client.py; this proves the widget wiring +
geometry.
"""
import json
from pathlib import Path

import pytest

from api_client import EndpointInfo, ModelEndpoints
from frontend_client import parse_performance, speed_tier

FIX = Path(__file__).parent / "fixtures"
OPUS = "anthropic/claude-4.8-opus-20260528"   # in the perf fixture; 0.375 tp / 0.125 lp
WARP = "openai/gpt-oss-120b"                   # tops the fixture field → WARP / elite


def _board():
    return parse_performance(json.loads(
        (FIX / "fe_rankings_performance.json").read_text(encoding="utf-8"))["data"])


def _ep(name, tag):
    return EndpointInfo(provider_name=name, tag=tag, latency_p50=900.0,
                        uptime_last_30m=100.0, throughput_p50=50.0,
                        pricing_prompt=1e-6, pricing_completion=5e-6)


def _card(perma=OPUS, model_id="anthropic/claude-opus-4.8", model_name="Claude Opus 4.8"):
    from widgets import PinnedModelCard
    card = PinnedModelCard(model_id)
    card.set_endpoints(ModelEndpoints(model_id=model_id, model_name=model_name,
                                      endpoints=[_ep("Anthropic", "anthropic")]))
    card.set_speed(_board().standing(perma))
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
def test_speed_band_adds_height_and_hit_target(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y",
                                      endpoints=[_ep("Anthropic", "anthropic")]))
    h0 = card.height()
    card.set_speed(_board().standing(OPUS))
    # band pill height + the gap below it before the provider rows
    assert card.height() == h0 + PinnedModelCard.SPEED_H + PinnedModelCard.ROWS_GAP
    card.resize(560, card.height())
    _render(card)
    assert not card._speed_hit_rect.isEmpty()


def test_no_speed_keeps_classic_card(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y",
                                      endpoints=[_ep("Anthropic", "anthropic")]))
    card.resize(560, card.height())
    _render(card)
    assert card.has_speed() is False
    assert card._speed_hit_rect.isEmpty()


def test_comet_x_encodes_throughput_percentile(qapp):
    """The hero read: the comet's x-position is the throughput percentile mapped
    onto the lane. This is the deterministic proof the band renders the data."""
    card = _card()
    _render(card)
    lane = card._speed_lane_rect
    tp = card._speed.throughput_pct           # 0.375
    expected = lane.left() + tp * lane.width()
    assert card._speed_marker_x == pytest.approx(expected, abs=0.6)
    assert lane.left() < card._speed_marker_x < lane.right()


def test_warp_comet_sits_at_the_finish(qapp):
    card = _card(perma=WARP, model_id="openai/gpt-oss-120b", model_name="gpt-oss-120b")
    _render(card)
    lane = card._speed_lane_rect
    # tp == 1.0 (fastest in the field) → comet at the far end of the lane.
    assert card._speed_marker_x == pytest.approx(lane.right(), abs=0.6)


def test_latency_lives_in_the_dossier_not_the_band(qapp):
    """The band is a single clean throughput meter — the latency axis is not a
    mystery marker on it; its full read lives in the click-through dossier."""
    card = _card()
    _render(card)
    assert card._speed_reaction_x is None
    assert "FIRST TOKEN" in card.speed_html()


def test_speed_band_paints_pixels(qapp):
    """The meter knob is not blank: it tints the card where the fill ends."""
    from theme import Colors
    card = _card()
    img = _render(card)
    cx = int(card._speed_marker_x)
    cy = int(card._speed_hit_rect.center().y())
    px = img.pixelColor(cx, cy)
    bg = Colors.BG_CARD
    assert (px.red(), px.green(), px.blue()) != (bg.red(), bg.green(), bg.blue())


def test_speed_accent_is_the_tier_color(qapp):
    card = _card()
    _render(card)
    assert card.speed_accent() == speed_tier(card._speed.throughput_pct)[1]


def test_warp_tier_is_elite_and_drives_shimmer(qapp):
    card = _card(perma=WARP, model_id="openai/gpt-oss-120b", model_name="gpt-oss-120b")
    assert card._speed_elite is True
    card.show()
    qapp.processEvents()
    assert card._shimmer_timer.isActive()    # elite throughput → heat-haze sparkle
    card.hide()
    qapp.processEvents()
    assert not card._shimmer_timer.isActive()


def test_dropping_speed_removes_band_and_stops_shimmer(qapp):
    card = _card(perma=WARP, model_id="openai/gpt-oss-120b", model_name="gpt-oss-120b")
    card.show()
    qapp.processEvents()
    h_with = card.height()
    card.set_speed(None)
    assert card.has_speed() is False
    # removing the band drops its pill height AND the gap below it
    assert card.height() == h_with - card.SPEED_H - card.ROWS_GAP
    assert not card._shimmer_timer.isActive()


def test_speed_dossier_is_auditable(qapp):
    card = _card()
    _render(card)
    h = card.speed_html()
    assert "faster than 38%" in h                        # round(0.375*100)
    assert "STREAM SPEED" in h and "FIRST TOKEN" in h    # plain-English axis labels
    assert "Fastest stream" in h and "Anthropic" in h    # best_throughput_provider
    assert "Fastest first token" in h and "Google" in h  # best_latency_provider
    assert "9-model field" in h                           # field_size from fixture
    assert "BRISK" in h                                   # 0.375 → BRISK tier
    assert "#6/9" in h                                    # throughput rank cell


def test_speed_dossier_flags_axis_divergence(qapp):
    """Opus streams mid-field (38th) but is slow to first token (12th) → the
    verdict must call out the contradiction."""
    card = _card()
    _render(card)
    assert "slower to first token" in card.speed_html()


def test_speed_dossier_empty_without_data(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    assert card.speed_html() == ""
    assert card.speed_accent() == "#00d2ff"


class _Press:
    """Minimal left-button press at a point — mousePressEvent only reads
    .button() and .position(), so this avoids the deprecated QMouseEvent ctor."""
    def __init__(self, pos):
        from PySide6.QtCore import Qt
        self._pos, self._btn = pos, Qt.MouseButton.LeftButton
    def button(self):
        return self._btn
    def position(self):
        return self._pos


def test_crest_and_speed_bands_share_columns(qapp):
    """Alignment lock: when both the Arena crest and the speed band are shown,
    their emblems sit on ONE vertical column and their content starts on ONE
    column — the exact misalignment the user flagged (bolt 5px off the hexagon)."""
    from api_client import BenchmarkEntry, CategoryStanding
    card = _card()                       # already has a speed standing
    e = BenchmarkEntry(display_name="Claude Opus 4.8")
    e.standings = [CategoryStanding(category="website", elo=1379, win_rate=58,
                                    rank=1, field_size=40)]
    card.set_benchmark(e)
    card.resize(560, card.height())
    _render(card)
    # emblem columns (hexagon center vs bolt center) align
    assert card._speed_emblem_cx == pytest.approx(card._crest_emblem_cx, abs=0.5)
    # content columns (crest text start vs speed lane start) align
    assert card._speed_lane_rect.left() == pytest.approx(card._crest_content_x, abs=0.5)


def test_speed_click_emits_with_model_id(qapp):
    card = _card()
    _render(card)
    seen = []
    card.speed_clicked.connect(lambda mid, pos: seen.append(mid))
    card.mousePressEvent(_Press(card._speed_hit_rect.center()))
    assert seen == ["anthropic/claude-opus-4.8"]
