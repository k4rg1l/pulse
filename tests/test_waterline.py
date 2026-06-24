"""Deterministic render tests for #6 THE WATERLINE (the hidden-cost iceberg).

Drives a headless (offscreen) QApplication and MEASURES the rendered waterline —
the class-collapse depth, that a clean row paints NOTHING, the implicit-caching
buoy (incl. the +4px best-row nudge), the discount==0 exclusion, that the strip
never overlaps the price digits, and the click signal — rather than eyeballing a
screenshot (MEMORY: validation must be deterministic). The pure class-collapse
logic is also unit-tested here against the F1 fixtures.

Mirrors tests/test_threshold_door.py / tests/test_speed_percentile.py.
"""
import json
from pathlib import Path

import pytest

from api_client import (EndpointInfo, ModelEndpoints, parse_model_endpoints,
                        hidden_fee_classes, hidden_fee_depth, HIDDEN_MAX)

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _ep(name, *, extra=None, sic=False, prompt=5e-6, completion=1e-5,
        tput=50.0, lat=900.0, up=100.0):
    return EndpointInfo(provider_name=name, tag=name.lower(),
                        pricing_prompt=prompt, pricing_completion=completion,
                        throughput_p50=tput, latency_p50=lat, uptime_last_30m=up,
                        supports_implicit_caching=sic,
                        pricing_extra=dict(extra or {}))


def _card(eps, model_id="x/y", model_name="Y", width=560):
    from widgets import PinnedModelCard
    card = PinnedModelCard(model_id)
    card.set_endpoints(ModelEndpoints(model_id=model_id, model_name=model_name,
                                      endpoints=eps))
    card.resize(width, card.height())
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


def _is_strip_teal(c):
    """Discriminate the waterline strip's steel/abyss/edge teal (green-leaning,
    blue<200) from the PRICE TEXT's pure cyan (0,210,255 — blue==255) and from
    the faint best-row CYAN highlight wash (~26,41,64 — green<90). Surface is
    (47,125,138), abyss (14,77,92), edge (127,214,224): all green>red+30, green
    >=90, blue<200."""
    r, g, b = c.red(), c.green(), c.blue()
    return c.alpha() > 0 and b < 200 and g > r + 30 and g >= 90


def _count_strip_teal(img, x0, x1, y0=0, y1=None):
    if y1 is None:
        y1 = img.height()
    return sum(1 for yy in range(int(y0), int(y1))
               for xx in range(int(x0), int(x1))
               if _is_strip_teal(img.pixelColor(xx, yy)))


# ===========================================================================
#  PURE LAYER — the class collapse (decisions A/B), exercised on the F1
#  fixtures + constructed endpoints. (No Qt needed, but kept here with the
#  render tests as the feature's single suite.)
# ===========================================================================
def test_class_collapse_cache_and_search_is_two_fifths():
    """cache_read + cache_write COLLAPSE to ONE 'cache' class; with web_search
    the set is {'cache','search'} (size 2) and depth == 2/5 (decision A)."""
    ep = _ep("P", extra={"input_cache_read": 5e-7, "input_cache_write": 6e-6,
                         "web_search": 0.01})
    classes = hidden_fee_classes(ep)
    assert classes == frozenset({"cache", "search"})
    assert len(classes) == 2
    assert hidden_fee_depth(classes) == pytest.approx(2 / 5)


def test_class_collapse_reasoning_and_media():
    """internal_reasoning + image → {'reasoning','media'} (image/audio/audio-
    cache all collapse to the single 'media' class)."""
    ep = _ep("P", extra={"internal_reasoning": 1e-5, "image": 1.25e-6})
    assert hidden_fee_classes(ep) == frozenset({"reasoning", "media"})
    # audio / audio-cache also map to 'media' (one class, not three)
    ep2 = _ep("P", extra={"audio": 1e-6, "input_audio_cache": 1e-7})
    assert hidden_fee_classes(ep2) == frozenset({"media"})


def test_multimodal_fixture_has_all_four_classes():
    me = parse_model_endpoints("google/gemini-2.5-pro",
                               _load("endpoints_full_multimodal.json"))
    classes = hidden_fee_classes(me.endpoints[0])
    assert classes == frozenset({"cache", "search", "reasoning", "media"})
    assert hidden_fee_depth(classes) == pytest.approx(4 / 5)   # /5 headroom


def test_sparse_fixture_clean_row_is_empty():
    """gpt-4o/Azure (only prompt+completion+discount) → no hidden classes."""
    me = parse_model_endpoints("openai/gpt-4o", _load("endpoints_sparse.json"))
    azure = me.endpoints[0]
    assert hidden_fee_classes(azure) == frozenset()
    assert hidden_fee_depth(hidden_fee_classes(azure)) == 0.0


def test_discount_nonzero_does_not_count_as_a_class():
    """A real discount (0.25) is a price CUT, not a hidden fee — excluded."""
    ep = _ep("P", extra={"discount": 0.25})
    assert hidden_fee_classes(ep) == frozenset()
    # and a discount alongside real fees doesn't inflate the count
    ep2 = _ep("P", extra={"discount": 0.25, "web_search": 0.01})
    assert hidden_fee_classes(ep2) == frozenset({"search"})


def test_explicit_zero_and_absent_fees_excluded():
    """value==0 (explicit) and an absent key both fail to count (decision B)."""
    ep = _ep("P", extra={"web_search": 0.0})          # explicit zero
    assert hidden_fee_classes(ep) == frozenset()
    ep2 = _ep("P", extra={})                          # all absent
    assert hidden_fee_classes(ep2) == frozenset()


# ===========================================================================
#  (1) CLASS-COLLAPSE DEPTH — measured on the rendered card's introspection.
# ===========================================================================
def test_render_depth_cache_plus_search(qapp):
    ep = _ep("P", extra={"input_cache_read": 5e-7, "input_cache_write": 6e-6,
                         "web_search": 0.01})
    card = _card([ep])
    _render(card)
    ident = card._ep_ident(ep)
    assert card._waterline_fee_classes[ident] == frozenset({"cache", "search"})
    assert card._waterline_depth[ident] == pytest.approx(2 / 5)


def test_render_depth_reasoning_plus_media(qapp):
    ep = _ep("P", extra={"internal_reasoning": 1e-5, "image": 1.25e-6})
    card = _card([ep])
    _render(card)
    ident = card._ep_ident(ep)
    assert card._waterline_fee_classes[ident] == frozenset({"reasoning", "media"})
    assert card._waterline_depth[ident] == pytest.approx(2 / 5)


# ===========================================================================
#  (2) CLEAN ROW — depth 0, empty classes, NO hit rect, paints no strip.
# ===========================================================================
def test_clean_row_paints_nothing(qapp):
    """prompt+completion-only → depth 0, empty classes, no hit rect, and the
    pixels under the price are background (no strip drawn)."""
    ep = _ep("Solo", extra={})                 # clean
    card = _card([ep])
    img = _render(card)
    ident = card._ep_ident(ep)
    assert card._waterline_fee_classes[ident] == frozenset()
    assert card._waterline_depth[ident] == 0.0
    assert card.has_fees() is False
    # NO hit rect for this row (a clean row is not clickable)
    assert all(i != ident for _r, i, _c in card._waterline_hits)
    assert ident not in card._waterline_buoy_rects
    # No waterline-teal anywhere in the price column — nothing was painted.
    w, h = card.width(), card.height()
    assert _count_strip_teal(img, w - 90, w - 14) == 0


# ===========================================================================
#  (3) IMPLICIT-CACHING BUOY — drawn when True, absent when False/None.
# ===========================================================================
def test_buoy_drawn_when_implicit_caching_true(qapp):
    ep = _ep("P", extra={"web_search": 0.01}, sic=True)
    card = _card([ep])
    _render(card)
    ident = card._ep_ident(ep)
    assert card._waterline_buoy[ident] is True
    assert ident in card._waterline_buoy_rects       # a ring was recorded
    rect = card._waterline_buoy_rects[ident]
    assert not rect.isEmpty()
    # the ring sits in the far-left margin channel near PAD_X-4
    assert rect.center().x() == pytest.approx(card.PAD_X - 4 + 2.5, abs=0.6)


def test_buoy_absent_when_implicit_caching_false(qapp):
    ep = _ep("P", extra={"web_search": 0.01}, sic=False)
    card = _card([ep])
    _render(card)
    ident = card._ep_ident(ep)
    assert card._waterline_buoy[ident] is False
    assert ident not in card._waterline_buoy_rects


def test_buoy_alone_makes_row_clickable_even_when_clean(qapp):
    """A clean-fee row that DOES support implicit caching still draws the buoy
    and is clickable (there is something to decode: the caching footer)."""
    ep = _ep("P", extra={}, sic=True)          # no hidden fees, but buoy
    card = _card([ep])
    _render(card)
    ident = card._ep_ident(ep)
    assert card._waterline_fee_classes[ident] == frozenset()
    assert ident in card._waterline_buoy_rects
    assert any(i == ident for _r, i, _c in card._waterline_hits)


# ===========================================================================
#  (4) DISCOUNT==0 adds no class (rendered).
# ===========================================================================
def test_render_discount_zero_no_class_no_strip(qapp):
    ep = _ep("P", extra={"discount": 0.0})
    card = _card([ep])
    _render(card)
    ident = card._ep_ident(ep)
    assert card._waterline_fee_classes[ident] == frozenset()
    assert all(i != ident for _r, i, _c in card._waterline_hits)


# ===========================================================================
#  (5) STRIP never overlaps the digits: strip.top() >= price_baseline, and a
#      pixel ON the strip is teal while the digit baseline pixels are unchanged.
# ===========================================================================
def _price_geometry(card, ep, y):
    """Re-derive (price_baseline, tx, pw, strip_top) the SAME way the paint does
    so the test pins the contract, not a magic number."""
    from PySide6.QtGui import QFontMetrics
    from theme import Fonts
    fm = QFontMetrics(Fonts.mono_small())
    price_text = card._price(ep)
    pw = fm.horizontalAdvance(price_text)
    price_right = card.width() - card.PAD_X
    tx = price_right - pw
    price_baseline = y + (card.ROW_H + fm.ascent() - fm.descent()) / 2.0
    strip_top = price_baseline + 2.0
    return price_baseline, tx, pw, strip_top, price_right


def _first_row_y(card):
    """The y of the first (only) provider row given no bands are present."""
    return card.PAD_Y + card.HEADER_H


def test_strip_below_baseline_never_overlaps_digits(qapp):
    ep = _ep("P", extra={"input_cache_read": 5e-7, "web_search": 0.01,
                         "internal_reasoning": 1e-5, "image": 1e-6})  # 3 classes
    card = _card([ep])
    img = _render(card)
    y = _first_row_y(card)
    price_baseline, tx, pw, strip_top, price_right = _price_geometry(card, ep, y)

    # (a) the contract: the strip sits at/below the baseline (decision F).
    assert strip_top >= price_baseline

    # (b) the strip painted real teal in its band at/just-below the baseline —
    #     the pale-aqua sea-level edge line (green~181) is the reliable marker.
    #     Sample from the baseline through the 3px strip (a 1px line at strip_top
    #     rasterizes to the pixel just above it, hence start at baseline).
    on_strip = _count_strip_teal(img, tx, tx + pw,
                                 y0=price_baseline, y1=strip_top + 4)
    assert on_strip > 0, "no teal strip painted below the baseline"

    # (c) the digit body (clearly ABOVE the strip) carries NO strip-teal — the
    #     strip didn't ride up into the glyphs (decision F). Scan from the row
    #     top down to the baseline.
    teal_in_digits = _count_strip_teal(img, tx, price_right,
                                       y0=y, y1=price_baseline)
    assert teal_in_digits == 0, f"{teal_in_digits} teal pixels overlap the digits"


# ===========================================================================
#  (6) BUOY +4px-DOWN NUDGE on a best row vs a non-best row.
# ===========================================================================
def test_buoy_nudges_down_on_best_row(qapp):
    """The buoy centers on the row mid normally; on the BEST row (gold accent
    bar present) it shifts +4px DOWN to clear the bar (decision G). Same single
    endpoint rendered as best vs not-best yields buoy centers 4px apart."""
    # Non-best: two endpoints, the OTHER one is faster (lower latency) so it is
    # best — our buoy endpoint is NOT best.
    buoy_ep = _ep("Buoy", extra={"web_search": 0.01}, sic=True, lat=1200.0)
    faster = _ep("Faster", extra={}, sic=False, lat=300.0)
    card_nb = _card([buoy_ep, faster])
    _render(card_nb)
    assert card_nb._best is faster                       # buoy_ep is not best
    ident = card_nb._ep_ident(buoy_ep)
    y_nb = card_nb._waterline_buoy_rects[ident].center().y()

    # Best: the SAME buoy endpoint, now alone → it IS best (gold bar present).
    buoy_ep2 = _ep("Buoy", extra={"web_search": 0.01}, sic=True, lat=1200.0)
    card_b = _card([buoy_ep2])
    _render(card_b)
    assert card_b._best is buoy_ep2
    ident_b = card_b._ep_ident(buoy_ep2)
    y_b = card_b._waterline_buoy_rects[ident_b].center().y()

    # both are on the first provider row (same y origin), so the only difference
    # is the +4px best-row nudge.
    assert y_b == pytest.approx(y_nb + 4.0, abs=0.6), (y_nb, y_b)


# ===========================================================================
#  SETTINGS GATE — off → no strip, no hits, no buoy.
# ===========================================================================
def test_gate_off_paints_nothing(qapp):
    ep = _ep("P", extra={"web_search": 0.01, "internal_reasoning": 1e-5}, sic=True)
    card = _card([ep])
    _render(card)
    assert card.has_fees() is True                       # on by default
    card.set_show_fees(False)
    img = _render(card)
    assert card.has_fees() is False
    assert card._waterline_hits == []
    assert card._waterline_buoy_rects == {}
    # nothing teal painted in the price column
    w, h = card.width(), card.height()
    assert _count_strip_teal(img, w - 90, w - 14) == 0


def test_gate_toggles_back_on(qapp):
    ep = _ep("P", extra={"web_search": 0.01}, sic=False)
    card = _card([ep])
    card.set_show_fees(False)
    _render(card)
    assert card.has_fees() is False
    card.set_show_fees(True)
    _render(card)
    assert card.has_fees() is True
    ident = card._ep_ident(ep)
    assert card._waterline_fee_classes[ident] == frozenset({"search"})


# ===========================================================================
#  DOSSIER — fees_html content + empty for a clean row.
# ===========================================================================
def test_fees_html_lists_present_fees_and_caching(qapp):
    me = parse_model_endpoints("google/gemini-2.5-pro",
                               _load("endpoints_full_multimodal.json"))
    card = _card(me.endpoints, model_id="google/gemini-2.5-pro")
    _render(card)
    ident = card._ep_ident(me.endpoints[0])
    h = card.fees_html(ident)
    assert "WHAT THE STICKER PRICE HIDES" in h
    assert "that's the tip" in h
    for token in ("cache read", "cache write", "web search", "reasoning",
                  "image", "audio"):
        assert token in h, token
    assert "tokens you never see" in h            # the reasoning ratio note
    assert "$0.014/call" in h                     # web_search is $/call
    assert "implicit caching" in h


def test_fees_html_empty_for_clean_row(qapp):
    ep = _ep("Solo", extra={})
    card = _card([ep])
    _render(card)
    ident = card._ep_ident(ep)
    assert card.fees_html(ident) == ""
    assert card.fees_html("nonexistent") == ""


def test_fees_html_escapes_provider_name(qapp):
    """API-sourced provider/model names are HTML-escaped (mirrors door_html)."""
    ep = _ep("P&<script>", extra={"web_search": 0.01})
    card = _card([ep], model_name="Evil & <b>")
    _render(card)
    ident = card._ep_ident(ep)
    h = card.fees_html(ident)
    assert "<script>" not in h
    assert "&amp;" in h


# ===========================================================================
#  CLICK — the price-cell waterline emits fees_clicked with model_id + ident.
# ===========================================================================
class _Press:
    def __init__(self, pos):
        from PySide6.QtCore import Qt
        self._pos, self._btn = pos, Qt.MouseButton.LeftButton

    def button(self):
        return self._btn

    def position(self):
        return self._pos


def test_fees_click_emits_model_and_ident(qapp):
    ep = _ep("P", extra={"web_search": 0.01}, sic=False)
    card = _card([ep], model_id="anthropic/claude-opus-4.8")
    _render(card)
    ident = card._ep_ident(ep)
    rect = next(r for r, i, _c in card._waterline_hits if i == ident)
    seen = []
    card.fees_clicked.connect(lambda mid, idd, pos: seen.append((mid, idd)))
    card.mousePressEvent(_Press(rect.center()))
    assert seen == [("anthropic/claude-opus-4.8", ident)]


def test_clean_row_click_does_not_emit(qapp):
    ep = _ep("Solo", extra={})
    card = _card([ep])
    _render(card)
    seen = []
    card.fees_clicked.connect(lambda *a: seen.append(a))
    # click the price cell — but a clean row has no hit rect, so nothing fires
    from PySide6.QtCore import QPointF
    y = _first_row_y(card)
    card.mousePressEvent(_Press(QPointF(card.width() - 30, y + card.ROW_H / 2)))
    assert seen == []
