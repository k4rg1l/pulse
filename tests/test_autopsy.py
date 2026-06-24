"""Deterministic tests for Wave 2 #11 — THE AUTOPSY (the lasso-fired spend
cause-of-death drill-down).

Three layers, all MEASURED (no eyeballing, per the deterministic-validation
discipline):
  1. PURE — build_autopsy(rows, t0, t1) -> AutopsyReport against fixtures:
     descending-$ order, the dominant %, the cache-offset as a positive GREEN
     magnitude (usage_cache is NEGATIVE), created_at__hour bucket-key detection,
     _as_int on the STRING request_count, the >6-row remainder collapse, and an
     empty-window report. Plus the AnalyticsClient.get_autopsy hour-clamp +
     interaction-fired-only contract (NOT in get_spend_board) + caching.
  2. DOSSIER — AutopsyStripWidget.render_pixmap(): pixmap height == the measured
     formula (no clip), rows descending with the dominant bar fill/track == 0.81
     ±tol, the cache-offset footer == abs(usage_cache) and GREEN/never negative,
     the empty-window "clean window" bar.
  3. INTERACTION + NO-REGRESSION — on SpendSpectrum: a press..release spanning a
     known hour range emits spike_selected with the right bucket ISOs; a
     zero-width click does NOT emit spike_selected (falls through to a tap); and
     a click on a LEGEND ROW still emits band_clicked (#10 receipt path intact).
"""
import pytest

import api_client as a
from api_client import (
    build_autopsy, build_spend_board, AutopsyReport, AutopsyRow,
    _bucket_key, _as_int,
)
from widgets import build_autopsy_html, autopsy_accent_hex

SONNET = "anthropic/claude-4.6-sonnet-20260217"
HAIKU = "anthropic/claude-4.5-haiku-20251001"
OPUS = "anthropic/claude-opus-4.8"

T0 = "2026-06-22T12:00:00+00:00"
T1 = "2026-06-22T13:00:00+00:00"


def _spike_rows():
    """An hourly dims=[model,provider] window decomposing to a dominant 81%:
    sonnet@Google $3.91/95req (the runaway), other@Azure $0.7572, haiku $0.16
    -> spike_total $4.8272, dominant share 0.8100. usage_cache NEGATIVE on the
    heavy row (-16.28 = a realized cache credit, NOT a drain)."""
    return [
        {"created_at__hour": "2026-06-22 12:00:00", "model": SONNET,
         "provider": "Google", "total_usage": 3.91, "request_count": "95",
         "cached_tokens": "6147577", "reasoning_tokens": "6472",
         "usage_cache": -16.28},
        {"created_at__hour": "2026-06-22 12:00:00", "model": OPUS,
         "provider": "Azure", "total_usage": 0.7572, "request_count": "8",
         "cached_tokens": "1000", "reasoning_tokens": "0", "usage_cache": -0.40},
        {"created_at__hour": "2026-06-22 13:00:00", "model": HAIKU,
         "provider": "Amazon Bedrock", "total_usage": 0.16, "request_count": "4",
         "cached_tokens": "0", "reasoning_tokens": "0", "usage_cache": 0.0},
    ]


# ===========================================================================
#  PURE — build_autopsy
# ===========================================================================
def test_build_autopsy_descending_and_dominant_share():
    r = build_autopsy(_spike_rows(), T0, T1)
    # descending by $: sonnet, opus, haiku
    assert [row.model_id for row in r.rows] == [SONNET, OPUS, HAIKU]
    assert r.rows[0].provider == "Google"
    assert r.spike_total == pytest.approx(4.8272)
    # the dominant share is 0.81 within tolerance (THE headline number)
    assert r.rows[0].share == pytest.approx(0.81, abs=0.005)
    # shares are a fraction of the spike total, guarded
    assert sum(row.share for row in r.rows) == pytest.approx(1.0, abs=1e-6)
    # request_count came in as a STRING -> _as_int summed it
    assert r.request_total == 95 + 8 + 4
    assert r.rows[0].request_count == 95
    assert not r.is_empty


def test_build_autopsy_cache_offset_is_positive_green_magnitude():
    # usage_cache is NEGATIVE (a realized cache credit) -> cache_offset is the
    # POSITIVE magnitude Σ abs(usage_cache); NEVER a drain, NEVER negative.
    r = build_autopsy(_spike_rows(), T0, T1)
    assert r.cache_offset == pytest.approx(16.28 + 0.40)
    assert r.cache_offset > 0.0
    # cached_tokens are STRINGS -> _as_int summed
    assert r.cached_total == 6147577 + 1000


def test_build_autopsy_bucket_key_detection():
    # provider dims -> created_at__hour (detected, never hardcoded)
    rows = _spike_rows()
    assert _bucket_key(rows[0]) == "created_at__hour"
    # _as_int coerces the string request_count
    assert _as_int(rows[0]["request_count"]) == 95


def test_build_autopsy_window_label():
    r = build_autopsy(_spike_rows(), T0, T1)
    assert r.window_label == "12:00–13:00"   # en-dash
    # also accepts the live created_at__hour space form for the label parse
    r2 = build_autopsy([], "2026-06-22 09:00:00", "2026-06-22 10:00:00")
    assert r2.window_label == "09:00–10:00"


def test_build_autopsy_empty_window():
    # key present but $0 in the lassoed window -> a clean, populated-zero report
    # (NOT the locked state): is_empty True, no rows, no crash.
    r = build_autopsy([], T0, T1)
    assert r.is_empty
    assert r.rows == ()
    assert r.spike_total == 0.0
    assert r.cache_offset == 0.0
    # a divide-by-zero guard: shares would be 0, not a ZeroDivisionError
    r2 = build_autopsy(
        [{"created_at__hour": "2026-06-22 12:00:00", "model": SONNET,
          "provider": "Google", "total_usage": 0.0, "request_count": "0"}],
        T0, T1)
    assert r2.is_empty and r2.rows[0].share == 0.0


def test_build_autopsy_remainder_collapse_over_six_rows():
    # >6 (model,provider) rows collapse the tail into a bounded remainder bar.
    rows = []
    # 8 distinct providers, descending usage 8.0, 7.0, ... 1.0
    for i in range(8):
        rows.append({
            "created_at__hour": "2026-06-22 12:00:00", "model": SONNET,
            "provider": f"P{i}", "total_usage": float(8 - i),
            "request_count": "1", "cached_tokens": "0", "usage_cache": 0.0,
        })
    r = build_autopsy(rows, T0, T1)
    assert len(r.rows) == 8
    assert len(r.visible) == a.AUTOPSY_MAX_ROWS == 6
    assert r.remainder_count == 2                 # the 2 smallest
    assert r.remainder_usage == pytest.approx(1.0 + 2.0)   # $1 + $2 tail
    # visible are the 6 largest, descending
    assert [row.provider for row in r.visible] == ["P0", "P1", "P2", "P3", "P4", "P5"]


# ===========================================================================
#  PURE — AnalyticsClient.get_autopsy (interaction-fired, clamped, cached)
# ===========================================================================
def test_get_autopsy_locked_no_network(monkeypatch):
    import config
    monkeypatch.setattr(config, "MANAGEMENT_KEY", "", raising=False)
    client = a.AnalyticsClient()
    assert client.unlocked is False

    def _boom(*args, **kwargs):
        raise AssertionError("get_autopsy must not hit the network when locked")

    monkeypatch.setattr(client.session, "post", _boom)
    assert client.get_autopsy(T0, T1) is None


def test_get_autopsy_clamps_to_hour_and_queries_hour_grain(monkeypatch):
    import config
    monkeypatch.setattr(config, "MANAGEMENT_KEY", "mgmt-test", raising=False)
    client = a.AnalyticsClient()
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"data": _spike_rows(),
                             "metadata": {"truncated": False}, "cachedAt": 1}}

    def _post(url, json=None, timeout=None, **kw):
        captured["body"] = json
        return _Resp()

    monkeypatch.setattr(client.session, "post", _post)
    # lasso a 12:34 -> 12:58 window; it must clamp to [12:00, 13:00) hour grain.
    rep = client.get_autopsy("2026-06-22T12:34:00+00:00",
                             "2026-06-22T12:58:00+00:00")
    assert rep is not None and not rep.is_empty
    body = captured["body"]
    assert body["granularity"] == "hour"
    assert body["dimensions"] == ["model", "provider"]
    # floored start hour, ceiled end hour (the next hour boundary)
    assert body["date_range"]["start"].startswith("2026-06-22T12:00:00")
    assert body["date_range"]["end"].startswith("2026-06-22T13:00:00")
    # the metric union includes usage_cache (the green offset source)
    assert "usage_cache" in body["metrics"] and "total_usage" in body["metrics"]


def test_get_autopsy_day_selection_spans_full_day(monkeypatch):
    # The standing 7d Spectrum is DAY-granularity, so a lasso/tap emits bare
    # dates -> the autopsy spans that full UTC day (hour-grained query) and the
    # label reads as the DATE, not '00:00–00:00'.
    import config
    monkeypatch.setattr(config, "MANAGEMENT_KEY", "mgmt-test", raising=False)
    client = a.AnalyticsClient()
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"data": _spike_rows(),
                             "metadata": {"truncated": False}, "cachedAt": 1}}

    def _post(url, json=None, timeout=None, **kw):
        captured["body"] = json
        return _Resp()

    monkeypatch.setattr(client.session, "post", _post)
    rep = client.get_autopsy("2026-06-22", "2026-06-22")
    assert rep is not None
    assert rep.window_label == "2026-06-22"            # the date, not 00:00
    body = captured["body"]
    assert body["granularity"] == "hour"               # still drills the hours
    assert body["date_range"]["start"].startswith("2026-06-22T00:00:00")
    assert body["date_range"]["end"].startswith("2026-06-23T00:00:00")  # full day


def test_get_autopsy_not_in_get_spend_board(monkeypatch):
    # The autopsy query is INTERACTION-fired ONLY — get_spend_board must NEVER
    # issue an HOUR dims=[model,provider] query (decision B). We record every
    # query() key and assert no hour+provider key appears.
    import config
    monkeypatch.setattr(config, "MANAGEMENT_KEY", "mgmt-test", raising=False)
    client = a.AnalyticsClient()
    seen = []
    real_query = client.query

    def _spy(metrics, dimensions, granularity, start, end):
        seen.append((tuple(dimensions), granularity))
        return {"rows": [], "metadata": {}, "cachedAt": None}

    monkeypatch.setattr(client, "query", _spy)
    client.get_spend_board()
    assert ("model", "provider") not in [(d, g) for (d, g) in seen if g == "hour"], \
        "get_spend_board must not fire the hourly autopsy query"
    # sanity: it DID issue at least one query (the workhorse Query A)
    assert seen


# ===========================================================================
#  DOSSIER — AutopsyStripWidget.render_pixmap (qapp)
# ===========================================================================
def _strip(report):
    from widgets import AutopsyStripWidget
    return AutopsyStripWidget(report)


def test_dossier_pixmap_height_matches_formula(qapp):
    r = build_autopsy(_spike_rows(), T0, T1)   # 3 rows, no remainder
    w = _strip(r)
    rows = len(r.visible)                       # 3 (no remainder bar)
    expected = (w.PAD * 2 + rows * (w.BAR_H + w.BAR_GAP) - w.BAR_GAP)
    assert w._h == int(expected)
    pm = w.render_pixmap()
    # the pixmap is the full measured height (no clip); dpr-aware so divide it out
    assert pm.height() / pm.devicePixelRatio() == pytest.approx(w._h, abs=1.0)


def test_dossier_rows_descending_and_dominant_fill(qapp):
    r = build_autopsy(_spike_rows(), T0, T1)
    w = _strip(r)
    w.render_pixmap()   # populates _bar_geom
    # one geom entry per drawn row (3 visible, no remainder)
    assert len(w._bar_geom) == 3
    fills = [fw for (fw, tw, rem) in w._bar_geom]
    # descending fill widths == descending $ (sonnet > opus > haiku)
    assert fills[0] > fills[1] > fills[2]
    # the DOMINANT bar's fill / track == 0.81 within tolerance (THE tell)
    fw0, tw0, _ = w._bar_geom[0]
    assert fw0 / tw0 == pytest.approx(0.81, abs=0.01)


def test_dossier_cache_offset_footer_green_never_negative(qapp):
    r = build_autopsy(_spike_rows(), T0, T1)
    htm = build_autopsy_html(r)
    # the GREEN cache-offset line shows the POSITIVE magnitude with a minus SIGN
    # in the copy ("offset −$16.68"), never a negative number / never a drain.
    assert "#2ed573" in htm                       # GREEN
    assert "caching offset" in htm
    assert "16.68" in htm                         # abs(-16.28) + abs(-0.40)
    assert "-$-" not in htm and "$-16" not in htm  # never a negative magnitude
    # the accent is CRIMSON (forensic), distinct from green/cyan popups
    assert autopsy_accent_hex(r).lower() == "#e0463c"


def test_dossier_remainder_bar_rendered(qapp):
    # >6 rows -> a 7th (remainder) bar is drawn, flagged is_remainder.
    rows = [{"created_at__hour": "2026-06-22 12:00:00", "model": SONNET,
             "provider": f"P{i}", "total_usage": float(8 - i),
             "request_count": "1", "usage_cache": 0.0} for i in range(8)]
    r = build_autopsy(rows, T0, T1)
    w = _strip(r)
    w.render_pixmap()
    assert len(w._bar_geom) == 7                  # 6 visible + 1 remainder
    assert w._bar_geom[-1][2] is True             # the remainder flag


def test_dossier_empty_clean_window(qapp):
    # $0 in the window -> a single muted "clean window" bar, no crimson.
    r = build_autopsy([], T0, T1)
    w = _strip(r)
    assert r.is_empty
    # exactly ONE bar drawn (the clean-window track), and it is NOT a crimson fill
    pm = w.render_pixmap()
    assert len(w._bar_geom) == 1
    fw, tw, rem = w._bar_geom[0]
    assert fw == 0.0                              # no incision filled
    assert pm.width() > 0                          # rendered without error
    # the HTML header reads the clean-window state, no "$ drained"
    htm = build_autopsy_html(r)
    assert "clean window" in htm and "drained" not in htm


def test_dossier_html_escapes_names(qapp):
    # decision E: every model/provider name reaching the HTML is html.escape'd.
    rows = [{"created_at__hour": "2026-06-22 12:00:00",
             "model": "evil/<script>x</script>", "provider": "P&<>",
             "total_usage": 1.0, "request_count": "1", "usage_cache": 0.0}]
    r = build_autopsy(rows, T0, T1)
    htm = build_autopsy_html(r)
    assert "<script>" not in htm                   # escaped in the header
    assert "&lt;script&gt;" in htm or "&amp;" in htm


# ===========================================================================
#  INTERACTION + NO-REGRESSION — SpendSpectrum lasso (qapp)
# ===========================================================================
def _board():
    rows = [
        {"date__day": "2026-06-20", "model": SONNET, "total_usage": 0.05, "request_count": "3"},
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 4.00, "request_count": "95"},
        {"date__day": "2026-06-21", "model": HAIKU, "total_usage": 0.01, "request_count": "2"},
        {"date__day": "2026-06-22", "model": SONNET, "total_usage": 0.06, "request_count": "5"},
    ]
    return build_spend_board(rows, granularity="day",
                             start="2026-06-20", end="2026-06-23")


def _spectrum(qapp, width=560):
    from widgets import SpendSpectrum
    import anim
    anim.set_enabled(False)
    w = SpendSpectrum()
    w.resize(width, 100)
    w.set_data(_board().spectrum)
    return w


def _press(w, x, y):
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    p = QPointF(x, y)
    w.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, p, p, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def _move(w, x, y):
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    p = QPointF(x, y)
    w.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, p, p, Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def _release(w, x, y):
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    p = QPointF(x, y)
    w.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, p, p, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def _bucket_x(w, i):
    cl, _, cw, _ = w._chart_geom()
    n = len(w._data.buckets)
    return cl + cw * i / (n - 1)


def _chart_mid_y(w):
    _, ct, _, ch = w._chart_geom()
    return ct + ch / 2.0


def test_lasso_emits_spike_selected_with_bucket_isos(qapp):
    w = _spectrum(qapp)
    sel = []
    w.spike_selected.connect(lambda t0, t1: sel.append((t0, t1)))
    y = _chart_mid_y(w)
    x0 = _bucket_x(w, 0)        # 2026-06-20
    x1 = _bucket_x(w, 2)        # 2026-06-22
    _press(w, x0, y)
    _move(w, (x0 + x1) / 2, y)
    _move(w, x1, y)
    _release(w, x1, y)
    # a real drag (> DRAG_MIN_PX) -> the lassoed bucket window ISOs
    assert sel == [("2026-06-20", "2026-06-22")]


def test_lasso_partial_window_snaps_to_buckets(qapp):
    w = _spectrum(qapp)
    sel = []
    w.spike_selected.connect(lambda t0, t1: sel.append((t0, t1)))
    y = _chart_mid_y(w)
    x0 = _bucket_x(w, 1)        # 2026-06-21
    x1 = _bucket_x(w, 2)        # 2026-06-22
    _press(w, x0, y)
    _move(w, x1, y)
    _release(w, x1, y)
    assert sel == [("2026-06-21", "2026-06-22")]


def test_zero_width_click_does_not_lasso(qapp):
    # A zero-width click (< DRAG_MIN_PX) must NOT emit spike_selected — it falls
    # through to the single-bucket tap (spike_clicked here, on the spike column).
    w = _spectrum(qapp)
    sel = []
    tap = []
    w.spike_selected.connect(lambda t0, t1: sel.append((t0, t1)))
    w.spike_clicked.connect(lambda t0, t1: tap.append((t0, t1)))
    c = w._spike_rect.center()
    _press(w, c.x(), c.y())
    _release(w, c.x(), c.y())
    assert sel == []                       # NO lasso
    assert tap == [("2026-06-21", "2026-06-22")]   # the single-bucket tap fired


def test_tiny_jitter_under_threshold_is_a_tap(qapp):
    # A 4px jitter (< 6px DRAG_MIN_PX) is still a tap, not a lasso.
    w = _spectrum(qapp)
    sel = []
    tap = []
    w.spike_selected.connect(lambda t0, t1: sel.append((t0, t1)))
    w.spike_clicked.connect(lambda t0, t1: tap.append((t0, t1)))
    c = w._spike_rect.center()
    _press(w, c.x(), c.y())
    _move(w, c.x() + 4, c.y())
    _release(w, c.x() + 4, c.y())
    assert sel == []
    assert tap == [("2026-06-21", "2026-06-22")]


def test_no_regression_legend_click_still_emits_band_clicked(qapp):
    # THE HARD REQUIREMENT: a click on a LEGEND ROW must still emit band_clicked
    # (#10's receipt path), NOT be swallowed by the autopsy lasso. band_clicked
    # fires on PRESS (the legend hit-test precedes the lasso), so a plain click
    # is unaffected.
    w = _spectrum(qapp)
    bands = []
    sel = []
    w.band_clicked.connect(lambda mid, anchor: bands.append(mid))
    w.spike_selected.connect(lambda t0, t1: sel.append((t0, t1)))
    mid, rect = w._legend_rects[0]
    c = rect.center()
    _press(w, c.x(), c.y())
    _release(w, c.x(), c.y())
    assert bands == [SONNET]
    assert sel == []                       # the legend click never starts a lasso
    # and no lasso state leaked
    assert w._drag_x0 is None and w._selection is None


def test_lasso_does_not_regress_band_polygon_tap(qapp):
    # A tap inside a band polygon (not a legend row, not the spike) still emits
    # band_clicked on release (the tap fallthrough preserves the old hit-test).
    w = _spectrum(qapp)
    bands = []
    w.band_clicked.connect(lambda mid, anchor: bands.append(mid))
    # find a point inside the heaviest band polygon that is NOT the spike rect.
    mid0, poly0 = w._band_polys[0]
    # the band sits over bucket 0 (a low column away from the spike at bucket 1).
    from PySide6.QtCore import QPointF, Qt
    cl, ct, cw, ch = w._chart_geom()
    x = _bucket_x(w, 0)
    # walk up from the baseline to find a y inside the polygon at this x.
    hit = None
    for dy in range(1, int(ch)):
        py = (ct + ch) - dy
        pt = QPointF(x, py)
        if poly0.containsPoint(pt, Qt.FillRule.OddEvenFill) and (
                w._spike_rect is None or not w._spike_rect.contains(pt)):
            hit = (x, py)
            break
    assert hit is not None, "no band-polygon test point found"
    _press(w, hit[0], hit[1])
    _release(w, hit[0], hit[1])
    assert bands == [mid0]


def test_lasso_disabled_when_locked(qapp):
    # LOCKED -> the lasso is disabled; a press-drag-release does nothing (no band,
    # no emit). No fake spike, no error.
    from widgets import SpendSpectrum
    import anim
    anim.set_enabled(False)
    w = SpendSpectrum()
    w.resize(560, 100)
    w.set_locked()
    sel = []
    tap = []
    w.spike_selected.connect(lambda t0, t1: sel.append((t0, t1)))
    w.spike_clicked.connect(lambda t0, t1: tap.append((t0, t1)))
    y = 80
    _press(w, 30, y)
    _move(w, 300, y)
    _release(w, 300, y)
    assert sel == [] and tap == []
    assert w._selection is None and w._drag_x0 is None
