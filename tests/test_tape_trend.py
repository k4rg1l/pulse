"""Deterministic tests for #7 THE TAPE (the torn-ticker momentum cartouche).

Two layers, mirroring test_speed_percentile.py:
  * PURE: the parser (rankings/models → {permaslug: change|None}) and the
    _trend_stamp / _trend_slope_sign helpers — exact strings pinned by COMPUTING
    the format output (decision A), never guessed.
  * QAPP: drives a headless card and MEASURES the rendered cartouche — the slope
    geometry vs the trend, non-collision with the relocated best-chip, silent
    degrade when there's no trend, the explosive shimmer opt-in, and a sampled
    pixel proving the trace paints in the amber/violet lane.
"""
import json
from pathlib import Path

import pytest

from PySide6.QtCore import Qt

from api_client import EndpointInfo, ModelEndpoints
from frontend_client import parse_rankings_models, TrendBoard

FIX = Path(__file__).parent / "fixtures"

OPUS_PERMA = "anthropic/claude-4.8-opus-20260528"   # +0.4253… riser (pinned model)
GLM_PERMA = "z-ai/glm-5.2-20260616"                 # +56.65 explosive (pinned model)
POOLSIDE_PERMA = "poolside/laguna-xs.2-20260421"    # +247.5 explosive new entrant
NEMOTRON_PERMA = "nvidia/nemotron-nano-9b-v2"       # -0.9753 faller
FABLE_PERMA = "anthropic/claude-5-fable-20260609"   # -1.0 dying
LLAMA_PERMA = "meta-llama/llama-3.3-70b-instruct"   # +0.012 near-flat
NORTH_PERMA = "cohere/north-mini-code-20260617"     # change=null → None


def _board() -> TrendBoard:
    return parse_rankings_models(json.loads(
        (FIX / "fe_rankings_models.json").read_text(encoding="utf-8"))["data"])


# ===========================================================================
#  PURE — parser
# ===========================================================================
def test_parser_keys_by_permaslug_and_keeps_change_floats():
    b = _board()
    assert b.change(OPUS_PERMA) == pytest.approx(0.4253374838839868)
    assert b.change(GLM_PERMA) == pytest.approx(56.651346410311056)
    assert b.change(POOLSIDE_PERMA) == pytest.approx(247.5472116814958)
    assert b.change(NEMOTRON_PERMA) == pytest.approx(-0.975319580248895)
    assert b.change(FABLE_PERMA) == pytest.approx(-1.0)


def test_parser_null_change_is_none_distinct_from_absent():
    b = _board()
    # ranked but no delta → None …
    assert b.change(NORTH_PERMA) is None
    # … and a model simply not on the board → None too (one silent-degrade path)
    assert b.change("nobody/not-a-model-20990101") is None
    assert b.change(None) is None
    assert b.change("") is None


def test_parser_tolerates_junk_rows():
    b = parse_rankings_models([
        {"model_permaslug": "a/b-1", "change": "0.5"},   # stringy float → float
        {"model_permaslug": "c/d-2", "change": "n/a"},   # non-numeric → None
        {"model_permaslug": "", "change": 1.0},          # no key → dropped
        "not-a-dict",                                     # ignored
        {"change": 9.0},                                 # no permaslug → dropped
    ])
    assert b.change("a/b-1") == 0.5
    assert b.change("c/d-2") is None
    assert len(b) == 2


def test_parser_empty_input():
    assert len(parse_rankings_models([])) == 0
    assert len(parse_rankings_models(None)) == 0


# ===========================================================================
#  PURE — _trend_stamp (decision A: COMPUTE the exact format, then assert it)
# ===========================================================================
def test_trend_stamp_exact_strings():
    from widgets import _trend_stamp
    # Normal band: assert the LITERAL output of f"{change:+.0%}" (never a guess).
    assert _trend_stamp(0.505) == f"{0.505:+.0%}"        # == "+50%"
    assert _trend_stamp(0.505) == "+50%"
    assert _trend_stamp(-0.975) == f"{-0.975:+.0%}"      # == "-98%" (round-half-away)
    assert _trend_stamp(-0.975) == "-98%"
    assert _trend_stamp(0.4253374838839868) == f"{0.4253374838839868:+.0%}"  # "+43%"
    # Explosive band (change > 5): "Nx" multiplier, clamped at 999x.
    assert _trend_stamp(247.0) == "+247x"
    assert _trend_stamp(247.5472116814958) == f"+{min(round(247.5472116814958), 999)}x"
    assert _trend_stamp(247.5472116814958) == "+248x"
    assert _trend_stamp(2000) == "+999x"                 # clamp
    assert _trend_stamp(5.0001) == "+5x"                 # just over the boundary
    # Flat band (|change| < 0.03): a centered dash.
    assert _trend_stamp(0.01) == "~"
    assert _trend_stamp(-0.029) == "~"
    assert _trend_stamp(0.0) == "~"
    # No data → empty (paints nothing).
    assert _trend_stamp(None) == ""


def test_trend_slope_sign_thresholds():
    from widgets import _trend_slope_sign
    assert _trend_slope_sign(0.505) == 1
    assert _trend_slope_sign(0.031) == 1
    assert _trend_slope_sign(247.0) == 1
    assert _trend_slope_sign(-0.975) == -1
    assert _trend_slope_sign(-0.031) == -1
    assert _trend_slope_sign(0.029) == 0      # inside the flat band
    assert _trend_slope_sign(-0.029) == 0
    assert _trend_slope_sign(0.0) == 0
    assert _trend_slope_sign(None) == 0


# ===========================================================================
#  QAPP — drive a headless card and MEASURE the rendered cartouche
# ===========================================================================
def _ep(name="Anthropic", tag="anthropic"):
    return EndpointInfo(provider_name=name, tag=tag, latency_p50=900.0,
                        uptime_last_30m=100.0, throughput_p50=50.0,
                        pricing_prompt=1e-6, pricing_completion=5e-6)


def _card(change, w=560, name="Claude Opus 4.8", best=True):
    from widgets import PinnedModelCard
    card = PinnedModelCard("anthropic/claude-opus-4.8")
    card.set_endpoints(ModelEndpoints(model_id="anthropic/claude-opus-4.8",
                                      model_name=name, endpoints=[_ep()]))
    if best:
        card._best = _ep()          # the ★ best-chip (now relocated inline)
    card.set_trend(change)
    card.resize(w, card.height())
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


def _trace_screen_pts(card):
    """Reproduce _paint_tape's UNIT→screen mapping so a test can compare the
    head (latest, rightmost) tick's y against the tail's. Mirrors the paint
    math exactly (the hit rect is QRectF(tape_left-3, cy-9, …, 18))."""
    hr = card._tape_hit_rect
    tape_left = hr.left() + 3
    cy = hr.center().y()
    slot_l = tape_left + card.TAPE_HPAD / 2.0
    trace_h = card.TAPE_H - 6.0
    top = cy - trace_h / 2.0
    return [(slot_l + p.x() * card.TAPE_TRACE_W, top + p.y() * trace_h)
            for p in card._tape_trace_pts]


# --- (1) the stamp is wired into the card + measured once on set_trend --------
def test_card_measures_stamp_on_set_trend(qapp):
    from widgets import _trend_stamp
    card = _card(0.505)
    assert card._trend_stamp == _trend_stamp(0.505) == "+50%"
    card.set_trend(-0.975)
    assert card._trend_stamp == "-98%"
    card.set_trend(247.5472116814958)
    assert card._trend_stamp == "+248x"
    card.set_trend(0.01)
    assert card._trend_stamp == "~"


# --- (2) SLOPE encodes the trend: riser climbs (head higher), faller drops ----
def test_riser_slope_climbs_up_right(qapp):
    card = _card(0.505)
    _render(card)
    assert card._tape_slope_sign == 1
    pts = _trace_screen_pts(card)
    head_y, tail_y = pts[-1][1], pts[0][1]
    assert head_y < tail_y          # head sits HIGHER on screen (smaller y)


def test_faller_slope_drops_down_right(qapp):
    card = _card(-0.975)
    _render(card)
    assert card._tape_slope_sign == -1
    pts = _trace_screen_pts(card)
    head_y, tail_y = pts[-1][1], pts[0][1]
    assert head_y > tail_y          # head sits LOWER on screen (larger y)


def test_flat_slope_is_a_centered_dash(qapp):
    card = _card(0.01)
    _render(card)
    assert card._tape_slope_sign == 0
    pts = _trace_screen_pts(card)
    assert pts[-1][1] == pytest.approx(pts[0][1])      # head y == tail y
    assert card._trend_stamp == "~"


def test_steeper_slope_for_bigger_change(qapp):
    """Magnitude is geometric: a bigger riser has a steeper trace span."""
    small = _card(0.10)
    big = _card(0.90)
    def span(c):
        pts = _trace_screen_pts(c)
        return abs(pts[-1][1] - pts[0][1])
    assert span(big) > span(small)


# --- the cartouche actually paints in the amber / violet lane -----------------
def test_riser_head_dot_is_amber(qapp):
    card = _card(0.505)
    img = _render(card)
    hx, hy = _trace_screen_pts(card)[-1]
    px = img.pixelColor(int(round(hx)), int(round(hy)))
    # exact lane amber #F4B740 = (244,183,64); at minimum red >> blue
    assert px.alpha() > 0
    assert (px.red(), px.green(), px.blue()) == (244, 183, 64)


def test_faller_head_dot_is_violet_not_red(qapp):
    card = _card(-0.975)
    img = _render(card)
    hx, hy = _trace_screen_pts(card)[-1]
    px = img.pixelColor(int(round(hx)), int(round(hy)))
    assert px.alpha() > 0
    # violet-grey: blue dominates red (NEVER a red outage color where red>>blue)
    assert px.blue() > px.red()


# --- (3) NON-COLLISION: relocated chip + name boundary clear the Tape ---------
def test_long_name_does_not_collide_with_tape(qapp):
    """Narrow card + a very long model name + the ★ best-chip present: the
    elided-name rect must NOT intersect the Tape's hit rect (the header-math
    relocation holds)."""
    from PySide6.QtGui import QFontMetrics
    from theme import Fonts
    card = _card(0.505, w=240,
                 name="some-absurdly-long-model-name-that-keeps-going-and-going",
                 best=True)
    _render(card)
    assert not card._tape_hit_rect.isEmpty()
    # Recompute the elided-name + inline-chip layout the way paint does and
    # assert NOTHING in the header text run intersects the Tape's hit rect.
    name_fm = QFontMetrics(Fonts.subheading())
    tape_left = card._tape_hit_rect.left() + 3
    group_right = tape_left - card.GAP_NAME_TO_CHIP
    chip_w = QFontMetrics(Fonts.tiny()).horizontalAdvance(
        f"★ {card._best.provider_name}")
    # paint drops the chip when the floored name + chip can't both fit
    budget = int(group_right - chip_w - card.GAP_NAME_TO_CHIP - card.PAD_X)
    chip_shown = budget >= 40
    name_max_w = budget if chip_shown else max(40, int(group_right - card.PAD_X))
    elided = name_fm.elidedText(card._display_model_name(),
                                Qt.TextElideMode.ElideRight, name_max_w)
    name_right = card.PAD_X + name_fm.horizontalAdvance(elided)
    # the name's right edge stays left of the Tape's hit rect
    assert name_right <= card._tape_hit_rect.left()
    # and the inline chip — IF shown — also clears the Tape
    if chip_shown:
        chip_right = name_right + card.GAP_NAME_TO_CHIP + chip_w
        assert chip_right <= card._tape_hit_rect.left() + 1


def test_chip_relocates_inline_right_of_name(qapp):
    """Decision C: on a roomy card the ★ best-chip sits INLINE (right of the
    elided name), NOT in the far-right gutter — that gutter now holds the Tape.
    The chip's left edge is right of the name and well left of the Tape."""
    from PySide6.QtGui import QFontMetrics
    from theme import Fonts
    card = _card(0.505, w=560, name="Claude Opus 4.8", best=True)
    _render(card)
    name_fm = QFontMetrics(Fonts.subheading())
    elided = name_fm.elidedText(card._display_model_name(),
                                Qt.TextElideMode.ElideRight, 10_000)
    name_right = card.PAD_X + name_fm.horizontalAdvance(elided)
    chip_left = name_right + card.GAP_NAME_TO_CHIP
    chip_w = QFontMetrics(Fonts.tiny()).horizontalAdvance(
        f"★ {card._best.provider_name}")
    # chip is inline (just right of the name) …
    assert chip_left > name_right
    # … and its right edge clears the Tape's hit rect (no collision)
    assert chip_left + chip_w <= card._tape_hit_rect.left() + 1


def test_tape_adds_no_height(qapp):
    """Decision D: set_trend never reflows — the cartouche lives in header slack."""
    from widgets import PinnedModelCard
    plain = PinnedModelCard("anthropic/claude-opus-4.8")
    plain.set_endpoints(ModelEndpoints(model_id="anthropic/claude-opus-4.8",
                                       model_name="Claude Opus 4.8", endpoints=[_ep()]))
    h0 = plain.height()
    plain.set_trend(0.505)
    assert plain.height() == h0
    plain.set_trend(56.65)      # explosive
    assert plain.height() == h0
    plain.set_trend(None)
    assert plain.height() == h0


# --- (4) SILENT DEGRADE: no trend → empty hit rect + nothing paints -----------
def test_no_trend_paints_nothing(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("anthropic/claude-opus-4.8")
    card.set_endpoints(ModelEndpoints(model_id="anthropic/claude-opus-4.8",
                                      model_name="Claude Opus 4.8", endpoints=[_ep()]))
    card._best = _ep()
    card.resize(560, card.height())
    _render(card)
    assert card.has_trend() is False
    assert card._tape_hit_rect.isEmpty()
    assert card.trend_html() == ""


def test_dropping_trend_clears_the_tape(qapp):
    card = _card(0.505)
    _render(card)
    assert not card._tape_hit_rect.isEmpty()
    card.set_trend(None)
    _render(card)
    assert card.has_trend() is False
    assert card._tape_hit_rect.isEmpty()


# --- (5) EXPLOSIVE drives the shimmer; ordinary risers do not -----------------
def test_explosive_sets_flag_and_wants_shimmer(qapp):
    card = _card(56.65)
    assert card._trend_explosive is True
    assert card._wants_shimmer() is True
    card.show()
    qapp.processEvents()
    assert card._shimmer_timer.isActive()
    card.hide()
    qapp.processEvents()


def test_ordinary_riser_does_not_wake_shimmer(qapp):
    card = _card(0.505)
    assert card._trend_explosive is False
    # a lone riser does not request the shimmer (no Arena/Speed/uptime here)
    assert card._wants_shimmer() is False
    card.show()
    qapp.processEvents()
    assert not card._shimmer_timer.isActive()
    card.hide()
    qapp.processEvents()


# --- click → trend_clicked emits with the model id ----------------------------
class _Press:
    def __init__(self, pos):
        from PySide6.QtCore import Qt as _Qt
        self._pos, self._btn = pos, _Qt.MouseButton.LeftButton
    def button(self):
        return self._btn
    def position(self):
        return self._pos


def test_tape_click_emits_with_model_id(qapp):
    card = _card(0.505)
    _render(card)
    seen = []
    card.trend_clicked.connect(lambda mid, pos: seen.append(mid))
    card.mousePressEvent(_Press(card._tape_hit_rect.center()))
    assert seen == ["anthropic/claude-opus-4.8"]


# --- dossier auditability -----------------------------------------------------
def test_trend_dossier_riser(qapp):
    card = _card(0.505)
    h = card.trend_html()
    assert "RISER" in h
    assert "+50%" in h
    assert "more people are routing here" in h        # verdict
    assert "data:image/png;base64," in h               # the 2-point ramp img


def test_trend_dossier_faller_foreshadows_derank(qapp):
    card = _card(-0.975)
    h = card.trend_html()
    assert "FALLER" in h or "Cratering" in h
    assert "derank" in h                               # foreshadows #8
    assert "-98%" in h


def test_trend_dossier_explosive_rocket_or_noise(qapp):
    card = _card(247.5472116814958)
    h = card.trend_html()
    assert "NEW ENTRANT" in h
    assert "+248x" in h
    assert "rocket-or-noise" in h


def test_trend_dossier_empty_without_data(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    assert card.trend_html() == ""
    assert card.trend_accent() == "#9b8ccb"


# --- end-to-end distribution through the dashboard (resolver + board) ---------
def test_dashboard_distributes_change_to_card(qapp):
    from dashboard import Dashboard
    from settings import Settings
    from persistence import History
    from frontend_client import PermaslugResolver
    dash = Dashboard(History(), Settings())
    dash.set_tracked_models(["anthropic/claude-opus-4.8", "z-ai/glm-5.2"])
    dash.update_permaslug_resolver(PermaslugResolver({
        "anthropic/claude-opus-4.8": OPUS_PERMA,
        "z-ai/glm-5.2": GLM_PERMA,
    }))
    dash.update_trend(_board())
    opus = dash._pinned_cards["anthropic/claude-opus-4.8"]
    glm = dash._pinned_cards["z-ai/glm-5.2"]
    assert opus.has_trend() and opus._trend == pytest.approx(0.4253374838839868)
    assert glm.has_trend() and glm._trend_explosive is True   # +56.65
    dash.deleteLater()


def test_dashboard_resolver_miss_is_silent(qapp):
    from dashboard import Dashboard
    from settings import Settings
    from persistence import History
    from frontend_client import PermaslugResolver
    dash = Dashboard(History(), Settings())
    dash.set_tracked_models(["who/dis"])
    dash.update_permaslug_resolver(PermaslugResolver({}))   # no mapping
    dash.update_trend(_board())
    card = dash._pinned_cards["who/dis"]
    assert card.has_trend() is False           # resolver miss → set_trend(None)
    dash.deleteLater()
