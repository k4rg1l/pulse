"""Deterministic tests for Wave 2 F3 (analytics parsers) + #9 THE SPECTRUM.

Two layers, both measured (no eyeballing, per the deterministic-validation
discipline):
  1. PURE F3 — the module-level analytics parsers against the verbatim recon
     row + a dims=[model] multi-row fixture (bucket-key detection both ways,
     string coercion, per-model totals, hero total, spike detection, the
     divide-by-zero guard).
  2. THE SPECTRUM widget — a headless qapp builds SpendSpectrum, set_data() a
     fixture board (2 models × 3 day-buckets incl a spike), grabs to a QImage,
     and MEASURES: fixed height == the formula (no clip), descending band
     order, the spike glow column x == the max-total bucket x, the legend rows
     + their $ strings, the stored polygons/rects, a synthesized spike click
     emits spike_clicked, the locked state (padlock + unlock copy, no fake $),
     and the populated-empty state ("$0.00" + "No spend in this range").
"""
import json
from pathlib import Path

import pytest

import api_client as a
from api_client import (
    _as_int, _as_float, _bucket_key, parse_analytics_query,
    build_spend_spectrum, build_spend_board, SpendBoard, SpendSpectrumData,
)

# The verbatim recon row (dims=[model,provider] -> created_at__day).
RECON_ROW = {
    "created_at__day": "2026-06-23",
    "model": "anthropic/claude-4.6-sonnet-20260217",
    "provider": "Google",
    "total_usage": 0.032701,
    "request_count": "1",
    "tokens_total": "8685",
    "tokens_prompt": "8673",
    "tokens_completion": "12",
}

SONNET = "anthropic/claude-4.6-sonnet-20260217"
HAIKU = "anthropic/claude-4.5-haiku-20251001"


def _model_fixture_rows():
    """dims=[model] day fixture: 2 models × 3 day-buckets, a clear spike on
    2026-06-21 (sonnet $4.00 + haiku $0.01)."""
    return [
        {"date__day": "2026-06-20", "model": SONNET, "total_usage": 0.05, "request_count": "3"},
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 4.00, "request_count": "95"},
        {"date__day": "2026-06-21", "model": HAIKU, "total_usage": 0.01, "request_count": "2"},
        {"date__day": "2026-06-22", "model": SONNET, "total_usage": 0.06, "request_count": "5"},
    ]


# ===========================================================================
#  PURE F3 PARSERS
# ===========================================================================
def test_bucket_key_detects_both_names():
    # dims=[model,provider] -> created_at__day
    assert _bucket_key(RECON_ROW) == "created_at__day"
    # dims=[model] -> date__day
    assert _bucket_key(_model_fixture_rows()[0]) == "date__day"
    # hour/week names also match the regex
    assert _bucket_key({"created_at__hour": "x", "model": "m"}) == "created_at__hour"
    assert _bucket_key({"date__week": "x"}) == "date__week"
    # no bucket key present
    assert _bucket_key({"model": "m", "total_usage": 1.0}) is None


def test_string_metric_coercion():
    # request_count / tokens_* are STRINGS; coerce defensively.
    assert _as_int(RECON_ROW["request_count"]) == 1
    assert _as_int("95") == 95
    assert _as_int("8685") == 8685
    assert _as_int("1.0") == 1          # float-shaped string
    assert _as_int(None) == 0 and _as_int("") == 0
    # total_usage stays a float (JSON number)
    assert isinstance(RECON_ROW["total_usage"], float)
    assert _as_float(RECON_ROW["total_usage"]) == pytest.approx(0.032701)
    assert _as_float("0.5") == 0.5
    assert _as_float(None) == 0.0 and _as_float("") == 0.0


def test_parse_analytics_query_envelope():
    env = {"data": [RECON_ROW], "metadata": {"row_count": 1, "truncated": False},
           "cachedAt": 123}
    out = parse_analytics_query(env)
    assert out["rows"] == [RECON_ROW]
    assert out["metadata"]["row_count"] == 1
    assert out["cachedAt"] == 123
    # tolerates the fully-wrapped HTTP json too
    out2 = parse_analytics_query({"data": env})
    assert out2["rows"] == [RECON_ROW]
    # junk -> empty, never raises
    assert parse_analytics_query(None)["rows"] == []


def test_build_spend_spectrum_totals_and_spike():
    sp = build_spend_spectrum(_model_fixture_rows(), granularity="day")
    assert sp.buckets == ("2026-06-20", "2026-06-21", "2026-06-22")
    # descending spend: sonnet (rank 0, heaviest) then haiku
    assert [m.model_id for m in sp.models] == [SONNET, HAIKU]
    assert sp.models[0].total_usage == pytest.approx(4.11)
    assert sp.models[1].total_usage == pytest.approx(0.01)
    assert sp.models[0].request_count == 103   # 3 + 95 + 5
    # hero total
    assert sp.total == pytest.approx(4.12)
    # shares are a fraction of the total (guarded)
    assert sp.models[0].share == pytest.approx(4.11 / 4.12)
    # spike = the 2026-06-21 bucket (index 1), summed across models
    assert sp.spike_index == 1
    assert sp.buckets[sp.spike_index] == "2026-06-21"
    assert sp.spike_total == pytest.approx(4.01)
    # matrix aligns to buckets
    assert sp.matrix[SONNET] == [pytest.approx(0.05), pytest.approx(4.00), pytest.approx(0.06)]
    assert sp.matrix[HAIKU] == [0.0, pytest.approx(0.01), 0.0]


def test_build_spend_spectrum_divide_by_zero_and_empty():
    # empty rows -> tidy zeroed spectrum, no crash, spike_index -1
    e = build_spend_spectrum([], granularity="day")
    assert e.total == 0.0 and e.models == () and e.buckets == ()
    assert e.spike_index == -1 and e.is_empty
    # a zero-usage / zero-request row must not divide by zero
    z = build_spend_spectrum(
        [{"date__day": "2026-06-21", "model": SONNET, "total_usage": 0.0, "request_count": "0"}],
        granularity="day")
    assert z.total == 0.0 and z.is_empty
    assert z.models[0].share == 0.0   # guarded, not ZeroDivisionError


def test_build_spend_spectrum_zero_fills_the_day_axis():
    """The API returns only days WITH usage. A one-day-old account used to
    render one giant full-chart block; with a parseable start/end the day
    axis is zero-filled so that day reads as a spike inside the real week."""
    rows = [
        {"date__day": "2026-07-01", "model": SONNET, "total_usage": 0.13,
         "request_count": "19"},
    ]
    sp = build_spend_spectrum(rows, granularity="day",
                              start="2026-06-25T12:00:00+00:00",
                              end="2026-07-01T12:00:00+00:00")
    assert sp.buckets == ("2026-06-25", "2026-06-26", "2026-06-27",
                          "2026-06-28", "2026-06-29", "2026-06-30",
                          "2026-07-01")
    assert sp.spike_index == 6                      # the real day, last slot
    assert sp.matrix[SONNET][:6] == [0.0] * 6       # zero-filled
    assert sp.matrix[SONNET][6] == pytest.approx(0.13)
    assert sp.total == pytest.approx(0.13)


def test_build_spend_spectrum_zero_fill_tolerates_junk_range():
    # unparseable start/end (the tests' "s"/"e" convention) -> rows-only axis.
    sp = build_spend_spectrum(_model_fixture_rows(), granularity="day",
                              start="s", end="e")
    assert sp.buckets == ("2026-06-20", "2026-06-21", "2026-06-22")
    # and an EMPTY range never fabricates buckets (the tidy empty state).
    e = build_spend_spectrum([], granularity="day",
                             start="2026-06-25", end="2026-07-01")
    assert e.buckets == () and e.is_empty


def test_analytics_client_locked_no_network(monkeypatch):
    # No management key -> unlocked False -> query()/get_spend_board return None
    # WITHOUT hitting the network (the LOCKED sentinel). We assert no POST is
    # attempted by making session.post explode if called.
    import config
    monkeypatch.setattr(config, "MANAGEMENT_KEY", "", raising=False)
    client = a.AnalyticsClient()
    assert client.unlocked is False

    def _boom(*args, **kwargs):
        raise AssertionError("query() must not hit the network when locked")

    monkeypatch.setattr(client.session, "post", _boom)
    assert client.query(["total_usage"], ["model"], "day", "s", "e") is None
    assert client.get_spend_board() is None


def test_analytics_client_caches_query(monkeypatch):
    # An unlocked client caches by (metrics,dims,gran,start,end): a second call
    # with the same key must NOT POST again (served from cache within TTL).
    import config
    monkeypatch.setattr(config, "MANAGEMENT_KEY", "mgmt-test", raising=False)
    client = a.AnalyticsClient()
    assert client.unlocked is True

    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"data": [RECON_ROW],
                             "metadata": {"truncated": False}, "cachedAt": 1}}

    def _post(*args, **kwargs):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(client.session, "post", _post)
    p1 = client.query(["total_usage"], ["model"], "day", "s", "e")
    p2 = client.query(["total_usage"], ["model"], "day", "s", "e")
    assert p1 is not None and p2 is not None
    assert p1["rows"] == [RECON_ROW]
    assert calls["n"] == 1   # second call served from cache, no 2nd POST


def test_build_spend_board_reserves_later_slots():
    board = build_spend_board(_model_fixture_rows(), granularity="day",
                              start="s", end="e")
    assert isinstance(board, SpendBoard)
    assert board.spectrum.total == pytest.approx(4.12)
    assert board.start == "s" and board.end == "e"
    # #10 THE TILL ROLL + #12 THE REBATE STUB now ride Query A -> .receipts and
    # .savings are populated from the SAME rows (one Receipt per active model;
    # the savings roll-up). The remaining feature slots stay reserved.
    assert len(board.receipts) == 2
    assert board.savings is not None        # #12 rides Query A now
    assert board.ghosts is None and board.budget is None


# ===========================================================================
#  THE SPECTRUM widget (qapp)
# ===========================================================================
def _board():
    return build_spend_board(_model_fixture_rows(), granularity="day",
                             start="2026-06-20", end="2026-06-23")


def _spectrum(qapp, data=None, width=560):
    from widgets import SpendSpectrum
    import anim
    anim.set_enabled(False)   # deterministic: no in-flight count-up during grab
    w = SpendSpectrum()
    w.resize(width, 100)
    if data is not None:
        w.set_data(data)
    return w


def _grab(w):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    img = QImage(w.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    w.render(p, QPoint(0, 0))
    p.end()
    return img


def test_spectrum_fixed_height_matches_formula(qapp):
    from PySide6.QtGui import QFontMetrics
    from theme import Fonts
    w = _spectrum(qapp, _board().spectrum)
    # formula = pad_top + header_h + 8 + chart_h + axis_lane + 8 + legend_block
    #           + pad_bottom  (2026-07-01: the dead reserved savings lane is
    #           gone; the x-axis dates own a real lane; rows are click-height;
    #           the header is ONE calm line, not the 22pt hero block)
    header_h = max(QFontMetrics(Fonts.label()).height(),
                   QFontMetrics(Fonts.metric()).height())
    legend_row_h = QFontMetrics(Fonts.body()).height() + 10
    legend_block = legend_row_h * 2          # exactly 2 models
    axis_h = QFontMetrics(Fonts.tiny()).height() + 4
    expected = (w.PAD_TOP + header_h + 8 + w.CHART_H + axis_h + 8 + legend_block
                + w.PAD_BOTTOM)
    assert w.height() == int(expected)
    # and the grab is full-size (no clip)
    img = _grab(w)
    assert img.height() == w.height()


def test_spectrum_axis_lane_separates_chart_and_legend(qapp):
    """The cramped-layout regression (owner report 2026-07-01): the x-axis date
    labels used to share their band with the first legend row. The model list
    must now start BELOW a full axis lane: gap >= axis_h + 8."""
    from PySide6.QtGui import QFontMetrics
    from theme import Fonts
    w = _spectrum(qapp, _board().spectrum)
    _, chart_top, _, chart_h = w._chart_geom()
    chart_bottom = chart_top + chart_h
    first_row_top = w._legend_rects[0][1].top()
    axis_h = QFontMetrics(Fonts.tiny()).height() + 4
    assert first_row_top - chart_bottom == axis_h + 8


def test_spectrum_band_order_descending(qapp):
    # bottom (heaviest) band's TOP edge must sit BELOW (larger y) the minor
    # band's top edge — i.e. the floor band is the dominant model.
    w = _spectrum(qapp, _board().spectrum)
    assert len(w._band_polys) == 2
    (m0, poly0), (m1, poly1) = w._band_polys
    assert m0 == SONNET and m1 == HAIKU       # rank 0 first
    # min-y of each polygon = its highest (top) edge point
    top0 = min(p.y() for p in poly0)          # sonnet top edge
    top1 = min(p.y() for p in poly1)          # haiku top edge (stacked above)
    assert top0 > top1                        # sonnet's top edge is LOWER on screen


def test_spectrum_bands_accumulate_over_time(qapp):
    """Spend-over-time contract (owner direction 2026-07-01): the stack's top
    edge is a RUNNING TOTAL — it never descends across buckets, and its final
    point sits at full chart height (the range total == the header amount)."""
    w = _spectrum(qapp, _board().spectrum)
    n = len(w._data.buckets)
    _, top_poly = w._band_polys[-1]            # topmost band
    ys = [top_poly[i].y() for i in range(n)]   # its TOP edge, left→right
    for a, b in zip(ys, ys[1:]):
        assert b <= a + 1e-6                   # y falls or holds: cum climbs
    cl, ct, cw, ch = w._chart_geom()
    assert ys[-1] == pytest.approx(ct + 4, abs=0.5)  # ends at full height


def test_spectrum_spike_column_at_max_bucket(qapp):
    w = _spectrum(qapp, _board().spectrum)
    sp = w._data
    assert sp.spike_index == 1
    assert w._spike_rect is not None
    # the spike rect's center x must equal the x of bucket index 1.
    chart_left, _, chart_w, _ = w._chart_geom()
    n = len(sp.buckets)
    expected_x = chart_left + chart_w * 1 / (n - 1)
    assert w._spike_rect.center().x() == pytest.approx(expected_x, abs=0.5)


def test_spectrum_legend_rows_and_amounts(qapp):
    w = _spectrum(qapp, _board().spectrum)
    # exactly 2 legend rows
    assert len(w._legend_rects) == 2
    ids = [mid for mid, _ in w._legend_rects]
    assert ids == [SONNET, HAIKU]
    # the $ strings the legend paints equal the summed per-model totals
    assert f"${w._data.models[0].total_usage:,.2f}" == "$4.11"
    assert f"${w._data.models[1].total_usage:,.2f}" == "$0.01"
    # the hero total string
    assert f"${w._data.total:,.2f}" == "$4.12"


def test_spectrum_spike_click_emits(qapp):
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    w = _spectrum(qapp, _board().spectrum)
    captured = []
    w.spike_clicked.connect(lambda t0, t1: captured.append((t0, t1)))
    # A TAP on the spike column = press + release at the SAME point (a zero-width
    # drag resolves on release to the single-bucket spike_clicked, NOT a lasso).
    c = w._spike_rect.center()
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(c), QPointF(c),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    rel = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(c), QPointF(c),
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(press)
    w.mouseReleaseEvent(rel)
    assert captured == [("2026-06-21", "2026-06-22")]


def test_spectrum_band_click_emits(qapp):
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    w = _spectrum(qapp, _board().spectrum)
    captured = []
    w.band_clicked.connect(lambda mid, anchor: captured.append(mid))
    # click inside the sonnet legend row
    mid, rect = w._legend_rects[0]
    c = rect.center()
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(c), QPointF(c),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert captured == [SONNET]


def test_spectrum_locked_state(qapp):
    # set_locked paints the padlock + unlock copy, keeps real fixed height,
    # and stores NO bands/legend rects (no fake numbers).
    w = _spectrum(qapp)
    h_before = w.height()
    w.set_locked()
    assert w._locked is True
    assert w._band_polys == [] and w._legend_rects == []
    assert w._spike_rect is None
    # the locked height is its own measure (a tad taller: 3 placeholder rows)
    assert w.height() > 0
    _grab(w)  # must paint without error
    # the unlock copy is the canonical phrase
    from widgets import SPEND_UNLOCK_BASE
    assert SPEND_UNLOCK_BASE == "Add a management key at openrouter.ai to unlock"


def test_spectrum_populated_empty_state(qapp):
    # key present but $0 in range -> real "$0.00" hero + "No spend in this
    # range", NOT the locked placeholder.
    empty = build_spend_board([], granularity="day")
    w = _spectrum(qapp, empty.spectrum)
    assert w._locked is False
    assert w._data.is_empty
    assert f"${w._data.total:,.2f}" == "$0.00"
    assert w._band_polys == []          # nothing to stack
    _grab(w)                            # paints the empty chrome without error


def test_spectrum_reveal_property_does_not_move_widget(qapp):
    # the reveal Property must NOT be a QWidget builtin (pos/geometry) — setting
    # it changes the grow factor only, never the widget geometry.
    w = _spectrum(qapp, _board().spectrum)
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_reveal(0.3)
    assert w.get_reveal() == pytest.approx(0.3)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before


# ===========================================================================
#  show_spend GATE — section hidden + update_spend is safe no-op
# ===========================================================================
def test_show_spend_false_hides_section_and_update_is_noop(qapp):
    """When show_spend=False the Spend section must NOT be built (spend_spectrum
    is absent) and update_spend(None) / update_spend(board) must be silent
    no-ops — never AttributeError.  Mirrors the show_door / show_drift gates."""
    from dashboard import Dashboard
    from persistence import History
    from settings import Settings

    d = Dashboard(History(), Settings(show_spend=False))

    # Section not built: the widget attribute must be absent (or None).
    assert getattr(d, "spend_spectrum", None) is None, (
        "spend_spectrum must not be created when show_spend=False"
    )

    # update_spend(None) must not raise.
    d.update_spend(None)

    # update_spend(board) must not raise either.
    board = build_spend_board(_model_fixture_rows(), granularity="day")
    d.update_spend(board)


def test_show_spend_true_builds_section(qapp):
    """Baseline: with show_spend=True (the default) spend_spectrum IS built."""
    from dashboard import Dashboard
    from persistence import History
    from settings import Settings

    d = Dashboard(History(), Settings(show_spend=True))
    assert getattr(d, "spend_spectrum", None) is not None, (
        "spend_spectrum must exist when show_spend=True"
    )
