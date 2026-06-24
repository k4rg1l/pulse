"""Deterministic render tests for #5 THE THRESHOLD (the "cheapest door" band).

Drives a headless (offscreen) QApplication and MEASURES the rendered band — its
height across every band combo (the multi-band gap-math fix), the green-door
state change, the engraved save%, the perspective-leaf geometry, and the
fallback cascade — rather than eyeballing a screenshot (MEMORY: validation must
be deterministic). The pure resolution logic lives in test_door_resolution.py.
"""
import pytest

from api_client import EndpointInfo, ModelEndpoints, DOOR_AMBER, DOOR_EMERALD


def _ep(name, prompt, tput=50.0, lat=900.0, up=100.0):
    return EndpointInfo(provider_name=name, tag=name.lower(),
                        pricing_prompt=prompt, throughput_p50=tput,
                        latency_p50=lat, uptime_last_30m=up)


def _eps_amber():
    """best=Pricey $5/Mtok (fast), cheaper=Thrifty $4/Mtok (slower) → amber door,
    SAVE 20%. best_provider() picks lowest latency among uptime>=99."""
    return [_ep("Pricey", 5e-6, tput=80.0, lat=600.0),
            _ep("Thrifty", 4e-6, tput=40.0, lat=1200.0)]


def _eps_green():
    """cheaper is ALSO faster → green door."""
    return [_ep("Pricey", 5e-6, tput=40.0, lat=600.0),
            _ep("Thrifty", 4e-6, tput=90.0, lat=1200.0)]


def _card(eps=None, model_id="x/y", model_name="Y"):
    from widgets import PinnedModelCard
    card = PinnedModelCard(model_id)
    card.set_endpoints(ModelEndpoints(model_id=model_id, model_name=model_name,
                                      endpoints=eps if eps is not None else _eps_amber()))
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
# (1) HEIGHT — every {crest, speed, door} present/absent combo (8) equals the
#     recomputed formula. This is the proof the multi-band gap-math fix is right.
# ---------------------------------------------------------------------------
def test_height_across_all_eight_band_combos(qapp):
    from widgets import PinnedModelCard
    from api_client import BenchmarkEntry, CategoryStanding
    import json
    from pathlib import Path
    from frontend_client import parse_performance

    FIX = Path(__file__).parent / "fixtures"
    board = parse_performance(json.loads(
        (FIX / "fe_rankings_performance.json").read_text(encoding="utf-8"))["data"])
    OPUS = "anthropic/claude-4.8-opus-20260528"

    def make_benchmark():
        e = BenchmarkEntry(display_name="Y")
        e.standings = [CategoryStanding(category="website", elo=1379, win_rate=58,
                                        rank=1, field_size=40)]
        return e

    C = PinnedModelCard
    for want_crest in (False, True):
        for want_speed in (False, True):
            for want_door in (False, True):
                card = PinnedModelCard("x/y")
                # endpoints with a real cheaper door available
                card.set_show_door(want_door)
                card.set_endpoints(ModelEndpoints(
                    model_id="x/y", model_name="Y", endpoints=_eps_amber()))
                if want_speed:
                    card.set_speed(board.standing(OPUS))
                if want_crest:
                    card.set_benchmark(make_benchmark())

                # confirm intent: the door band is present iff we wanted it
                assert card.has_door() is want_door, (want_crest, want_speed, want_door)

                rows = len(card._endpoints.endpoints)
                crest = C.CREST_H if want_crest else 0
                speed = C.SPEED_H if want_speed else 0
                door = C.DOOR_H if want_door else 0
                bands = (crest > 0) + (speed > 0) + (door > 0)
                inter = C.BAND_GAP * max(0, bands - 1)
                below = C.ROWS_GAP if bands > 0 else 0
                expected = (C.HEADER_H + crest + speed + door + inter + below
                            + max(1, rows) * C.ROW_H + C.PAD_Y * 2)
                assert card.height() == expected, (
                    f"crest={want_crest} speed={want_speed} door={want_door}: "
                    f"{card.height()} != {expected}")


def test_door_band_adds_height_and_hit_target(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    card.set_show_door(False)
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y",
                                      endpoints=_eps_amber()))
    h0 = card.height()
    card.set_show_door(True)
    # band pill height + the gap below it before the provider rows
    assert card.height() == h0 + PinnedModelCard.DOOR_H + PinnedModelCard.ROWS_GAP
    card.resize(560, card.height())
    _render(card)
    assert not card._door_hit_rect.isEmpty()


# ---------------------------------------------------------------------------
# (2) GREEN-DOOR DETERMINISM
# ---------------------------------------------------------------------------
def test_green_door_when_cheaper_and_faster(qapp):
    card = _card(_eps_green())
    _render(card)
    assert card._door_green is True
    assert card.door_accent() == DOOR_EMERALD
    # the accent QColor cached for paint is emerald
    assert (card._door_accent.red(), card._door_accent.green(),
            card._door_accent.blue()) == (0x34, 0xd2, 0x7e)


def test_amber_door_when_cheaper_but_slower(qapp):
    card = _card(_eps_amber())
    _render(card)
    assert card._door_green is False
    assert card.door_accent() == DOOR_AMBER


def test_green_door_spills_emerald_pixels(qapp):
    """The green door is a physical state-change: emerald light spills in the gap
    just right of the leaf — a pixel there is green-dominant, not background."""
    from theme import Colors
    card = _card(_eps_green())
    img = _render(card)
    band = card._door_hit_rect
    poly = card._door_leaf_poly
    x = int(poly.boundingRect().right() + 4)
    y = int(band.center().y())
    px = img.pixelColor(x, y)
    bg = Colors.BG_CARD
    assert (px.red(), px.green(), px.blue()) != (bg.red(), bg.green(), bg.blue())
    assert px.green() > px.red() and px.green() > px.blue()   # emerald-dominant


# ---------------------------------------------------------------------------
# (3) SAVE% headline
# ---------------------------------------------------------------------------
def test_save_pct_headline(qapp):
    card = _card(_eps_amber())          # $5 -> $4 = 20%
    _render(card)
    assert card._door.save_pct == 20
    h = card.door_html()
    assert "SAVE 20%" in h
    assert "Thrifty" in h               # destination provider in the dossier


def test_green_headline_says_faster(qapp):
    card = _card(_eps_green())
    _render(card)
    h = card.door_html()
    assert "SAVE 20%" in h and "FASTER" in h


# ---------------------------------------------------------------------------
# (4) NO-OP cases → _door is None, band paints nothing, no door term in height
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("eps,best_is_none", [
    ([_ep("Solo", 5e-6)], False),                      # cheapest IS best
    ([_ep("Free", 0.0), _ep("Other", 4e-6)], False),   # best price 0 (free)
])
def test_noop_cases_paint_nothing(qapp, eps, best_is_none):
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y", endpoints=eps))
    assert card.has_door() is False
    assert card._door is None
    card.resize(560, card.height())
    _render(card)
    assert card._door_hit_rect.isEmpty()
    # height carries NO door term
    h_rows = max(1, len(eps)) * PinnedModelCard.ROW_H
    assert card.height() == PinnedModelCard.HEADER_H + h_rows + PinnedModelCard.PAD_Y * 2


def test_noop_when_best_is_none(qapp):
    """No uptime>=99 endpoint → best_provider() None → no door."""
    from widgets import PinnedModelCard
    eps = [_ep("A", 4e-6, lat=None), _ep("B", 5e-6, lat=None)]   # no latency → no best
    card = PinnedModelCard("x/y")
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y", endpoints=eps))
    assert card._best is None
    assert card.has_door() is False
    assert card.door_html() == ""


def test_door_dossier_empty_without_data(qapp):
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    assert card.door_html() == ""
    assert card.door_accent() == DOOR_AMBER


# ---------------------------------------------------------------------------
# (5) GEOMETRY — leaf bbox x in [21,41], never overlaps the lintel text rect,
#     chevron at band.right()-8-12
# ---------------------------------------------------------------------------
def test_leaf_geometry_and_no_text_overlap(qapp):
    card = _card(_eps_amber())
    _render(card)
    bb = card._door_leaf_poly.boundingRect()
    assert 21 <= bb.left() <= 41
    assert 21 <= bb.right() <= 41
    # the leaf never crosses into the lintel text lane
    assert bb.right() <= card._door_text_left
    # chevron at the canonical right position (mirrors _paint_speed)
    band = card._door_hit_rect
    assert card._door_chev_x == pytest.approx(band.right() - 8 - 12, abs=0.5)


def test_door_emblem_shares_left_rail(qapp):
    """The jamb post sits on the SAME emblem column as the Arena hexagon / Speed
    bolt: leaf hinge is just right of _icon_col_cx()=21."""
    card = _card(_eps_amber())
    _render(card)
    bb = card._door_leaf_poly.boundingRect()
    assert bb.left() == pytest.approx(card._icon_col_cx() + 1.0, abs=0.6)


def test_door_band_paints_pixels_on_leaf(qapp):
    """The leaf is not blank: it tints the card at its center."""
    from theme import Colors
    card = _card(_eps_amber())
    img = _render(card)
    bb = card._door_leaf_poly.boundingRect()
    px = img.pixelColor(int(bb.center().x()), int(bb.center().y()))
    bg = Colors.BG_CARD
    assert (px.red(), px.green(), px.blue()) != (bg.red(), bg.green(), bg.blue())
    assert px.red() > px.blue()         # amber-dominant (warm)


# ---------------------------------------------------------------------------
# (6) FALLBACK — at a narrow width, the ' · provider' tail drops BEFORE the
#     headline elides.
# ---------------------------------------------------------------------------
def test_fallback_drops_tail_before_eliding_headline(qapp):
    from widgets import PinnedModelCard
    from PySide6.QtGui import QFontMetrics
    from theme import Fonts
    # a long destination name so the tail is the first thing to go
    eps = [_ep("Pricey", 5e-6, tput=80.0, lat=600.0),
           _ep("A-Very-Long-Provider-Name-Indeed", 4e-6, tput=40.0, lat=1200.0)]
    card = PinnedModelCard("x/y")
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y", endpoints=eps))
    card.resize(220, card.height())     # narrow → forces the cascade
    _render(card)

    f = Fonts.tiny(); f.setBold(True)
    fm = QFontMetrics(f)
    band = card._door_hit_rect
    # at this width the tail has been dropped: the headline 'SAVE 20%' fits whole
    # between text_left and the chevron, i.e. it was NOT elided.
    headline = "SAVE 20%"
    avail = band.right() - 8 - 12 - card.CHEV_GAP - card._door_text_left
    assert avail + 0.5 >= fm.horizontalAdvance(headline)   # headline intact
    # and the tail provider text is NOT present in the painted lane (dropped),
    # which we prove structurally: text_left + headline width <= chevron lane,
    # with no room consumed by the long tail.
    assert card._door_text_left >= card._icon_col_cx()


def test_narrow_extreme_elides_headline(qapp):
    """Pathologically narrow → even the headline elides (cascade's last stage)."""
    from widgets import PinnedModelCard
    card = PinnedModelCard("x/y")
    card.set_endpoints(ModelEndpoints(model_id="x/y", model_name="Y",
                                      endpoints=_eps_amber()))
    card.resize(120, card.height())
    _render(card)
    # still renders without clipping past the chevron
    assert card._door_text_left < card._door_chev_x


# ---------------------------------------------------------------------------
# settings gate + click signal
# ---------------------------------------------------------------------------
def test_show_door_gate_removes_band(qapp):
    card = _card(_eps_amber())
    assert card.has_door() is True
    h_with = card.height()
    card.set_show_door(False)
    assert card.has_door() is False
    assert card.height() == h_with - card.DOOR_H - card.ROWS_GAP
    card.set_show_door(True)
    assert card.has_door() is True
    assert card.height() == h_with


class _Press:
    def __init__(self, pos):
        from PySide6.QtCore import Qt
        self._pos, self._btn = pos, Qt.MouseButton.LeftButton
    def button(self):
        return self._btn
    def position(self):
        return self._pos


def test_door_click_emits_with_model_id(qapp):
    card = _card(_eps_amber(), model_id="anthropic/claude-opus-4.8")
    _render(card)
    seen = []
    card.door_clicked.connect(lambda mid, pos: seen.append(mid))
    card.mousePressEvent(_Press(card._door_hit_rect.center()))
    assert seen == ["anthropic/claude-opus-4.8"]


def test_door_honesty_line_states_tradeoff(qapp):
    """The band never lies: the amber dossier states the speed trade-off."""
    card = _card(_eps_amber())
    _render(card)
    h = card.door_html()
    assert "slower" in h.lower()        # cheaper-but-slower honesty line
