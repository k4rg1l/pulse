"""Deterministic tests for Wave 3 #17 — THE FLIGHT RECORDER (Token Odometer +
Records + Streak).

Two layers, all MEASURED (the deterministic-validation discipline — no eyeballing,
no flaky clicking):

  1. PURE token_recorder (no Qt) — the #17 TEST_PLAN math:
     (a) lifetime sum: 3 day-rows with tokens_total STRINGS '53460','6634418',
         '9258' -> lifetime == 6,697,136 via _as_int.
     (b) record day: max-by-total_usage == 2026-06-22 with $4.3675 / 6,634,418 tok
         / 97 req.
     (c) streak with gaps: Jun21,22,23 (Jun24 ABSENT) -> last-active-run == 3 AND
         ongoing-today == False; a non-contiguous {Jun21, Jun23} -> run == 1 (the
         gap breaks it — proving absent dates are treated as zero, NOT contiguous).
     (d) locked vs empty: zero rows -> is_empty (real-zeros 'No traffic logged
         yet'), distinct from the widget's LOCKED sentinel.
     (e) bucket-key drift: the builder finds the bucket via _bucket_key even when
         the key is created_at__day (NEVER hardcode date__day).

  2. THE WIDGET (qapp) — TokenRecorder: the 3-day fixture renders the 3 bands; the
     drum reads 6,697,136 (cell count == len(f'{lifetime:,}')); the run caption ==
     '3-DAY RUN'; the runway has 3 lit + 4 dark pads; set_locked -> the dimmed
     '— — —' sentinel (NOT '0'); the `_roll` count-up fires ONCE and is SKIPPED on
     a same-value re-poll; the `_roll` Property is DISTINCT (the widget doesn't
     move); the regular-weight fallback recompute fires on overflow.
"""
import datetime

import pytest

from token_recorder import (
    RecordDay, TokenRecord, build_token_recorder,
    _day_map, _last_active_run,
)
from api_client import _bucket_key


# --------------------------------------------------------------------------- #
#  Row fixtures (the live analytics shape: total_usage float; tokens_total /    #
#  request_count STRINGS; bucket key date__day). The exact LIVE magnitudes.     #
# --------------------------------------------------------------------------- #
def _row(day, usage, tokens, reqs, key="date__day"):
    return {key: day, "total_usage": usage,
            "tokens_total": str(tokens), "request_count": str(reqs)}


def _three_day_rows(key="date__day"):
    # The live young-account state: 3 active days; Jun-22 the spike. Order is
    # deliberately scrambled to prove sorting, not input order, drives results.
    return [
        _row("2026-06-23", 0.033294, 9258, 2, key),
        _row("2026-06-21", 0.055400, 53460, 6, key),
        _row("2026-06-22", 4.367500, 6634418, 97, key),
    ]


# A "today" that makes the live run a CLOSED run (Jun-24, after the last active
# day Jun-23) — matches the real machine date the recon captured.
TODAY_AFTER = datetime.date(2026, 6, 24)


# --------------------------------------------------------------------------- #
#  1. PURE — the #17 TEST_PLAN math                                            #
# --------------------------------------------------------------------------- #
def test_lifetime_sum_over_string_tokens():
    # TEST_PLAN (a): Σ tokens_total (STRINGS) == 6,697,136 via _as_int.
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    assert rec.lifetime_tokens == 6697136
    assert isinstance(rec.lifetime_tokens, int)
    # the spend / requests roll-ups too (float / int from STRINGS).
    assert rec.lifetime_spend == pytest.approx(4.456194, abs=1e-5)
    assert rec.lifetime_requests == 105            # 6 + 97 + 2
    assert rec.active_days == 3
    assert rec.is_empty is False


def test_record_day_is_max_by_spend():
    # TEST_PLAN (b): the record = max-by-total_usage day = 2026-06-22.
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    assert rec.record.date == "2026-06-22"
    assert rec.record.spend == pytest.approx(4.3675, abs=1e-4)
    assert rec.record.tokens == 6634418 and isinstance(rec.record.tokens, int)
    assert rec.record.reqs == 97


def test_record_picked_by_max_not_input_order():
    # The spike day wins even though it is fed in the MIDDLE of the list.
    rows = [
        _row("2026-06-21", 0.0554, 53460, 6),
        _row("2026-06-22", 4.3675, 6634418, 97),   # the spike, listed 2nd
        _row("2026-06-23", 0.0333, 9258, 2),
    ]
    rec = build_token_recorder(rows, today=TODAY_AFTER)
    assert rec.record.date == "2026-06-22"


def test_streak_last_active_run_three_not_ongoing_today():
    # TEST_PLAN (c) part 1: Jun21->22->23 contiguous (Jun24 ABSENT) -> the
    # last-active-run == 3 AND ongoing-today == False (today Jun-24 has no row, so
    # an ongoing claim would be falsifiable — decision B).
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    assert rec.streak_run == 3
    assert rec.last_active_date == "2026-06-23"
    assert rec.streak_is_ongoing_today is False


def test_streak_gap_breaks_run_proving_absent_is_zero():
    # TEST_PLAN (c) part 2: a deliberately NON-CONTIGUOUS set {Jun21, Jun23} (Jun22
    # MISSING) -> run == 1 (the gap breaks it). This proves absent dates are
    # treated as ZERO, NOT silently contiguous — the core correctness guard.
    rows = [
        _row("2026-06-21", 0.0554, 53460, 6),
        _row("2026-06-23", 0.0333, 9258, 2),       # Jun-22 omitted -> a gap
    ]
    rec = build_token_recorder(rows, today=TODAY_AFTER)
    assert rec.active_days == 2                     # both days counted
    assert rec.streak_run == 1                      # but the run is only Jun-23
    assert rec.last_active_date == "2026-06-23"


def test_streak_ongoing_today_true_only_when_last_is_today():
    # When the most-recent active date IS today, the flag flips True (the only
    # case an "ongoing" label is honest). Use a today == the last active date.
    rows = _three_day_rows()
    rec = build_token_recorder(rows, today=datetime.date(2026, 6, 23))
    assert rec.last_active_date == "2026-06-23"
    assert rec.streak_is_ongoing_today is True
    # the run is still 3 (Jun21->22->23) — the flag is independent of length.
    assert rec.streak_run == 3


def test_second_strip_latent_today_coincide():
    # decision E: the biggest-TOKEN day == the biggest-SPEND day today (Jun-22 is
    # both), so has_second_strip is False (ONE strip). When they DIVERGE it flips.
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    assert rec.record.date == "2026-06-22"
    assert rec.record_by_tokens.date == "2026-06-22"
    assert rec.has_second_strip is False
    # Construct a divergent case: a high-SPEND low-token day vs a high-token day.
    rows = [
        _row("2026-06-21", 9.99, 1000, 5),         # biggest spend, tiny tokens
        _row("2026-06-22", 0.01, 9_000_000, 3),    # biggest tokens, tiny spend
    ]
    rec2 = build_token_recorder(rows, today=TODAY_AFTER)
    assert rec2.record.date == "2026-06-21"            # by spend
    assert rec2.record_by_tokens.date == "2026-06-22"  # by tokens
    assert rec2.has_second_strip is True


def test_empty_zero_active_days():
    # TEST_PLAN (d): zero rows -> is_empty ('No traffic logged yet'), real zeros.
    rec = build_token_recorder([])
    assert rec.is_empty is True
    assert rec.lifetime_tokens == 0
    assert rec.streak_run == 0
    assert rec.record.is_empty is True
    rec2 = build_token_recorder(None)
    assert rec2.is_empty is True


def test_bucket_key_drift_created_at_day():
    # TEST_PLAN (e): the builder finds the bucket via _bucket_key even when the key
    # is created_at__day (NEVER hardcode date__day).
    rows = _three_day_rows(key="created_at__day")
    assert _bucket_key(rows[0]) == "created_at__day"
    rec = build_token_recorder(rows, today=TODAY_AFTER)
    assert rec.lifetime_tokens == 6697136
    assert rec.record.date == "2026-06-22"
    assert rec.streak_run == 3


def test_series_is_ascending_active_days():
    # The dossier timeline series carries every active day ascending.
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    dates = [d.date for d in rec.series]
    assert dates == ["2026-06-21", "2026-06-22", "2026-06-23"]
    assert all(isinstance(d, RecordDay) for d in rec.series)


def test_helpers_day_map_and_run():
    # _day_map keys are the active ISO dates; _last_active_run is gap-aware.
    rows = _three_day_rows()
    bk = _bucket_key(rows[0])
    dmap = _day_map(rows, bk)
    assert sorted(dmap.keys()) == ["2026-06-21", "2026-06-22", "2026-06-23"]
    assert _last_active_run(sorted(dmap.keys()), dmap) == 3
    # an empty map -> 0.
    assert _last_active_run([], {}) == 0


# --------------------------------------------------------------------------- #
#  2. THE WIDGET (qapp) — TokenRecorder                                        #
# --------------------------------------------------------------------------- #
def _recorder(qapp, rec=None, width=340, anim_on=False):
    from widgets import TokenRecorder
    import anim
    anim.set_enabled(anim_on)
    w = TokenRecorder()
    w.resize(width, w.sizeHint().height() or 160)
    if rec is not None:
        w.set_data(rec)
    w._build_geometry()      # force a geometry build at the test width (offscreen)
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


def test_widget_three_bands_render_and_drum_digits(qapp):
    # The 3-day fixture: the drum reads 6,697,136 -> the digit/comma cell count ==
    # len(f'{lifetime:,}'); the run caption == '3-DAY RUN'; 3 lit + 4 dark pads.
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    w = _recorder(qapp, rec)
    assert w._rec is not None
    assert w._rec.lifetime_tokens == 6697136
    # the odometer drum cells == the formatted string length (digits + commas).
    assert w._drum_string() == "6,697,136"
    assert len(w._cells) == len("6,697,136")        # 9 cells (7 digits + 2 commas)
    # the runway: 7 slots, 3 lit (the run) + 4 dark.
    assert w._runway_slots() == 7
    assert w._lit_count() == 3
    assert w._run_caption() == "3-DAY RUN"
    _grab(w)                                          # paints all 3 bands


def test_widget_run_caption_ended_vs_ongoing(qapp):
    # ongoing-today False -> 'N-DAY RUN' (last-active-run, the honest label; NEVER
    # an ongoing-today claim). When ongoing-today True, the caption may read 'DAY
    # RUN' as a live streak — but never claims today's activity it doesn't have.
    rec_closed = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    w = _recorder(qapp, rec_closed)
    assert w._rec.streak_is_ongoing_today is False
    assert w._run_caption() == "3-DAY RUN"


def test_widget_locked_shows_sentinel_not_zero(qapp):
    # set_locked() -> the dimmed drum sentinel '— — —' (NOT a zeroed '0' that
    # could read as real). decision D.
    w = _recorder(qapp, None)
    w.set_locked()
    assert w._locked is True
    assert w._rec is None
    assert w._drum_string() == TokenRecorder_LOCKED_SENTINEL()
    assert "0" not in w._drum_string()                # never a real-looking zero
    _grab(w)


def test_widget_empty_state_real_zeros(qapp):
    # key present, zero active days -> the tidy 'No traffic logged yet' instrument
    # (real zeros, honest — distinct from the locked sentinel).
    rec = build_token_recorder([])
    w = _recorder(qapp, rec)
    assert w._rec.is_empty is True
    assert w._locked is False
    _grab(w)


def test_widget_roll_fires_once_then_skips_same_value(qapp, monkeypatch):
    # The `_roll` count-up START is gated behind a value-CHANGED check: it runs on
    # the FIRST populated set_data (0 -> 6,697,136) and is SKIPPED when the SAME
    # lifetime re-arrives (a 15-min same-value re-poll repaints silently).
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    w = _recorder(qapp, anim_on=True)
    starts = []
    monkeypatch.setattr(w, "_start_roll", lambda: starts.append(1))
    # first populate -> a value CHANGE -> the roll is started ONCE.
    w.set_data(rec)
    assert w._last_lifetime == 6697136
    assert len(starts) == 1
    # re-arrive with the SAME lifetime (a fresh equal object) -> NO re-start.
    rec2 = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    w.set_data(rec2)
    assert len(starts) == 1                            # still ONE — the gate held
    assert w.get_roll() == pytest.approx(1.0)          # parked at rest
    # a DIFFERENT lifetime -> the gate opens and the roll starts again.
    rows3 = _three_day_rows() + [_row("2026-06-20", 1.0, 5_000_000, 10)]
    w.set_data(build_token_recorder(rows3, today=TODAY_AFTER))
    assert len(starts) == 2


def test_widget_roll_property_distinct_no_move(qapp):
    # `_roll` is a DISTINCT Property (NOT pos/size/geometry); setting it changes
    # only the drum fill, never the widget geometry (the no-move regression).
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    w = _recorder(qapp, rec)
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_roll(0.5)
    assert w.get_roll() == pytest.approx(0.5)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before
    # the displayed drum value during the roll = int(target * _roll), re-formatted.
    assert w._drum_string() == f"{int(6697136 * 0.5):,}"


def test_widget_anim_disabled_roll_parks_at_one(qapp):
    # reduce-motion -> _roll parked at 1.0 (no running anim, full value shown).
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    w = _recorder(qapp, rec, anim_on=False)
    assert w.get_roll() == pytest.approx(1.0)
    assert w._drum_string() == "6,697,136"


def test_widget_regular_weight_fallback_on_overflow(qapp):
    # TEST_PLAN (d) tail: a very large lifetime whose bold drum-row would overflow
    # the inner width -> the measure pass drops to the regular weight and pads
    # less, recomputing the row width (no clipping). We force a narrow width.
    big_rows = [_row("2026-06-22", 4.0, 999_999_999_999, 100)]   # 12 digits + 3 commas
    rec = build_token_recorder(big_rows, today=TODAY_AFTER)
    w = _recorder(qapp, rec, width=180)                # deliberately narrow
    assert w._drum_string() == "999,999,999,999"
    assert len(w._cells) == len("999,999,999,999")     # all cells measured
    # the overflow path set the compact flag so paint uses the regular weight.
    assert w._drum_compact is True
    _grab(w)


def test_widget_sizehint_measure_once(qapp):
    # The height is font-metric-driven and stable (one measure pass feeds paint +
    # sizeHint). The locked + populated heights MATCH so the section never jumps.
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    w = _recorder(qapp, rec)
    h_pop = w.sizeHint().height()
    assert w.height() == h_pop
    w.set_locked()
    assert w.sizeHint().height() == h_pop              # no jump locked vs populated


def test_widget_click_emits_recorder_clicked(qapp):
    # Clicking the card emits recorder_clicked(anchor_y_global) -> the dossier path.
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    w = _recorder(qapp, rec)
    fired = []
    w.recorder_clicked.connect(lambda y: fired.append(y))
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(20, 40),
                     QPointF(20, 40), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert len(fired) == 1


def test_recorder_dossier_html_escaped_and_complete(qapp):
    # The dossier HTML carries the lifetime totals + the record day + the streak
    # definition spelled out; the per-day timeline facts are QPainter-drawn into
    # the embedded pixmap (injection-safe), so they live in the STRIP rows.
    from widgets import build_recorder_dossier_html, RecorderDossierStrip
    rec = build_token_recorder(_three_day_rows(), today=TODAY_AFTER)
    html_str = build_recorder_dossier_html(rec)
    assert "6,697,136" in html_str                     # lifetime tokens
    assert "<img src='data:image/png;base64," in html_str   # the painted timeline
    assert "2026-06-22" in html_str                     # the record day
    assert "last-active run" in html_str.lower()        # the streak definition
    # the per-day timeline rows are measured into the strip.
    strip = RecorderDossierStrip(rec)
    assert len(strip._rows) == 3                         # one mini-bar per day


def test_recorder_dossier_html_escapes_injection(qapp):
    # A hostile date string can't break out of the HTML wrapper (html.escape).
    from widgets import build_recorder_dossier_html
    evil = TokenRecord(
        lifetime_tokens=10, lifetime_spend=1.0, lifetime_requests=1,
        record=RecordDay(date="<script>alert(1)</script>", spend=1.0,
                         tokens=10, reqs=1),
        record_by_tokens=RecordDay(date="<script>alert(1)</script>", spend=1.0,
                                   tokens=10, reqs=1),
        streak_run=1, streak_is_ongoing_today=False,
        last_active_date="<script>alert(1)</script>", active_days=1,
        series=[RecordDay(date="<script>alert(1)</script>", spend=1.0,
                          tokens=10, reqs=1)],
    )
    html_str = build_recorder_dossier_html(evil)
    assert "<script>" not in html_str
    assert "&lt;script&gt;" in html_str


def TokenRecorder_LOCKED_SENTINEL():
    # Tiny indirection so the test references the widget's sentinel constant
    # without importing it at module top (qapp not yet alive at collection time).
    from widgets import TokenRecorder
    return TokenRecorder.LOCKED_SENTINEL
