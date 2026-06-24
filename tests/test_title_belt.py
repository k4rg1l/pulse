"""Deterministic tests for Wave 3 #16 — THE TITLE BELT (Model of the Week).

Two layers, all MEASURED (the deterministic-validation discipline — no eyeballing,
no flaky clicking):

  1. PURE model_of_week (no Qt) — the #16 TEST_PLAN math:
     (a) champion selection: {sonnet usage=4.45, haiku usage=0.002} -> champion=
         sonnet, share≈1.00, tokens via _as_int from a STRING.
     (b) share denominator = champ/Σ WITHIN the latest bucket (a prior-week row
         with a different winner does NOT change this week's share).
     (c) WoW undefined: ONE bucket -> delta None + Week-1; TWO buckets -> a signed
         delta and the cartouche flag flips.
     (d) bucket-key drift: the parser finds the bucket via _bucket_key even when
         the key is created_at__week.
     (e) empty: zero buckets -> is_empty ('No spend yet this week').

  2. THE WIDGET (qapp) — ModelOfWeekBelt: a 1-bucket fixture renders the champion
     engraving + the 'WEEK 1' ribbon (no green/red arrow); a 2-bucket fixture
     lights the momentum cartouche; set_locked -> ghosted belt; the glint fires
     once on first set_data and is SKIPPED when the same champion re-arrives; the
     `glint` Property is DISTINCT (the widget doesn't move); the monogram fallback
     fires when no logo pixmap is available.
"""
import math

import pytest

from model_of_week import (
    ModelOfWeek, build_model_of_week, humanize_model, provider_of,
    _week_date_label,
)


# --------------------------------------------------------------------------- #
#  Row fixtures (the live analytics shape: total_usage float; tokens_total /    #
#  request_count STRINGS; bucket key date__week).                               #
# --------------------------------------------------------------------------- #
SONNET = "anthropic/claude-4.6-sonnet-20260217"
HAIKU = "anthropic/claude-4.5-haiku-20251001"
DEEPSEEK = "deepseek/deepseek-v4-flash"


def _row(week, model, usage, tokens, reqs, key="date__week"):
    return {key: week, "model": model,
            "total_usage": usage, "tokens_total": str(tokens),
            "request_count": str(reqs)}


def _one_bucket_rows():
    # The live young-account state: ONE week bucket, sonnet 100%, haiku trace.
    return [
        _row("2026-06-21", SONNET, 4.454111, 6695148, 102),
        _row("2026-06-21", HAIKU, 0.0021, 1988, 3),
    ]


# --------------------------------------------------------------------------- #
#  1. PURE — the #16 TEST_PLAN math                                            #
# --------------------------------------------------------------------------- #
def test_champion_selection_and_string_tokens():
    # TEST_PLAN (a): champion=sonnet, share≈1.00, tokens via _as_int from STRING.
    m = build_model_of_week(_one_bucket_rows())
    assert m.champion_id == SONNET
    assert m.champion_name == "Claude 4.6 Sonnet"
    assert m.provider == "anthropic"
    assert m.share == pytest.approx(1.00, abs=0.01)
    assert m.week_spend == pytest.approx(4.4541, abs=0.001)
    # tokens_total / request_count arrived as STRINGS -> coerced to int.
    assert m.week_tokens == 6695148 and isinstance(m.week_tokens, int)
    assert m.week_requests == 102 and isinstance(m.week_requests, int)
    assert m.week_count == 1
    # the runner-up is traced for the dossier.
    assert m.runner_up_id == HAIKU
    assert m.runner_up_spend == pytest.approx(0.0021, abs=0.0005)


def test_champion_picked_by_max_usage_not_input_order():
    # The champion is the MAX-usage model even when fed in the wrong order.
    rows = [
        _row("2026-06-21", HAIKU, 0.0021, 1988, 3),       # listed first
        _row("2026-06-21", SONNET, 4.454111, 6695148, 102),
    ]
    m = build_model_of_week(rows)
    assert m.champion_id == SONNET


def test_share_denominator_is_within_latest_bucket_only():
    # TEST_PLAN (b): adding a PRIOR-week row with a DIFFERENT winner must NOT
    # change THIS week's share (share = champ / Σ within the latest bucket only,
    # not across the 21-day range).
    this_week = _one_bucket_rows()
    m_alone = build_model_of_week(this_week)
    share_alone = m_alone.share

    with_prior = [
        # a big prior-week bucket dominated by a different model
        _row("2026-06-14", DEEPSEEK, 999.0, 9_000_000_000, 50_000),
        _row("2026-06-14", SONNET, 1.0, 1000, 10),
    ] + this_week
    m_prior = build_model_of_week(with_prior)
    # the latest bucket is still 2026-06-21 and sonnet's share in it is unchanged
    assert m_prior.bucket_label == "2026-06-21"
    assert m_prior.champion_id == SONNET
    assert m_prior.share == pytest.approx(share_alone, abs=1e-6)
    assert m_prior.share == pytest.approx(1.0, abs=0.01)   # NOT diluted by 999.0


def test_wow_undefined_one_bucket_is_week_one():
    # TEST_PLAN (c) part 1: ONE bucket -> wow_delta None + the Week-1 state.
    m = build_model_of_week(_one_bucket_rows())
    assert m.week_count == 1
    assert m.wow_delta is None
    assert m.wow_rank_delta is None
    assert m.is_week_one is True
    assert m.is_empty is False


def test_wow_signed_delta_two_buckets():
    # TEST_PLAN (c) part 2: TWO buckets -> a SIGNED delta is produced and the
    # Week-1 flag flips off. Sonnet's share rises 0.80 -> ~1.00 (+~0.20).
    rows = [
        # prior week: sonnet 80% (champ then too), deepseek 20%
        _row("2026-06-14", SONNET, 8.0, 8_000_000, 80),
        _row("2026-06-14", DEEPSEEK, 2.0, 2_000_000, 20),
        # this week: sonnet ~100%
        _row("2026-06-21", SONNET, 4.454111, 6695148, 102),
        _row("2026-06-21", HAIKU, 0.0021, 1988, 3),
    ]
    m = build_model_of_week(rows)
    assert m.week_count == 2
    assert m.is_week_one is False
    assert m.wow_delta is not None
    assert m.wow_delta == pytest.approx(1.0 - 0.8, abs=0.01)   # +0.20 share
    assert m.wow_delta > 0                                     # a WIN ribbon


def test_wow_negative_delta_when_share_falls():
    # A losing week: sonnet's share falls 1.00 -> 0.60 (-0.40) -> a red cartouche.
    rows = [
        _row("2026-06-14", SONNET, 10.0, 10_000_000, 100),    # 100% prior
        _row("2026-06-21", SONNET, 6.0, 6_000_000, 60),       # 60% now
        _row("2026-06-21", DEEPSEEK, 4.0, 4_000_000, 40),
    ]
    m = build_model_of_week(rows)
    assert m.week_count == 2
    assert m.wow_delta == pytest.approx(0.6 - 1.0, abs=0.01)   # -0.40
    assert m.wow_delta < 0


def test_bucket_key_drift_created_at_week():
    # TEST_PLAN (d): the parser finds the bucket via _bucket_key even when the key
    # is created_at__week (NEVER hardcode date__week).
    rows = [
        _row("2026-06-21", SONNET, 4.45, 6695148, 102, key="created_at__week"),
        _row("2026-06-21", HAIKU, 0.002, 1988, 3, key="created_at__week"),
    ]
    m = build_model_of_week(rows)
    assert m.champion_id == SONNET
    assert m.week_count == 1
    assert m.bucket_label == "2026-06-21"
    assert m.is_week_one is True


def test_empty_zero_buckets():
    # TEST_PLAN (e): no rows -> is_empty ('No spend yet this week'), no champion.
    m = build_model_of_week([])
    assert m.is_empty is True
    assert m.champion_id == ""
    assert m.week_count == 0
    m2 = build_model_of_week(None)
    assert m2.is_empty is True


def test_humanize_and_provider_helpers():
    assert humanize_model(SONNET) == "Claude 4.6 Sonnet"
    assert humanize_model(HAIKU) == "Claude 4.5 Haiku"
    assert provider_of(SONNET) == "anthropic"
    assert provider_of("noprefix-model") == ""
    assert _week_date_label("2026-06-21") == "Week of Jun 21 2026"
    # a non-ISO bucket value falls back to the raw label (defensive).
    assert _week_date_label("not-a-date") == "not-a-date"
    assert _week_date_label("") == ""


# --------------------------------------------------------------------------- #
#  2. THE WIDGET (qapp) — ModelOfWeekBelt                                      #
# --------------------------------------------------------------------------- #
def _belt(qapp, mow=None, width=360, anim_on=False, logo_store=None):
    from widgets import ModelOfWeekBelt
    import anim
    anim.set_enabled(anim_on)
    w = ModelOfWeekBelt()
    if logo_store is not None:
        w.set_logo_store(logo_store)
    w.resize(width, 120)
    if mow is not None:
        w.set_data(mow)
    w._build_geometry()      # force a geometry build at the test width (offscreen)
    return w


def test_widget_week_one_renders_champion_no_arrow(qapp):
    # A 1-bucket fixture -> the champion is engraved + the Week-1 ribbon shows,
    # and NO green/red momentum cartouche is drawn.
    m = build_model_of_week(_one_bucket_rows())
    w = _belt(qapp, m)
    assert w._mow is not None
    assert w._mow.champion_id == SONNET
    assert w._is_week_one() is True          # the Week-1 ribbon path
    assert w._has_momentum() is False        # no up/down arrow
    _grab(w)                                  # paints without crashing


def test_widget_two_bucket_lights_momentum_cartouche(qapp):
    # A 2-bucket fixture with a real signed delta -> the momentum cartouche flag
    # flips on and the Week-1 ribbon is gone.
    rows = [
        _row("2026-06-14", SONNET, 8.0, 8_000_000, 80),
        _row("2026-06-14", DEEPSEEK, 2.0, 2_000_000, 20),
        _row("2026-06-21", SONNET, 4.454111, 6695148, 102),
    ]
    m = build_model_of_week(rows)
    w = _belt(qapp, m)
    assert w._is_week_one() is False
    assert w._has_momentum() is True
    assert m.wow_delta > 0                    # a WIN (green) cartouche
    _grab(w)


def test_widget_locked_ghosted_no_champion(qapp):
    # set_locked() -> the ghosted grey belt; no champion engraved.
    w = _belt(qapp, None)
    w.set_locked()
    assert w._locked is True
    assert w._mow is None
    _grab(w)                                  # paints the locked silhouette


def test_widget_empty_state(qapp):
    # Zero buckets -> the tidy 'No spend yet this week' belt (no champion).
    m = build_model_of_week([])
    w = _belt(qapp, m)
    assert w._mow.is_empty is True
    _grab(w)


def test_widget_glint_fires_once_then_skips_same_champion(qapp, monkeypatch):
    # The glint START is gated behind a champion-CHANGED check: it runs on the
    # FIRST populated set_data (None -> SONNET) and is SKIPPED when the SAME
    # champion re-arrives (a 15-min same-champion re-poll repaints silently).
    m = build_model_of_week(_one_bucket_rows())
    w = _belt(qapp, anim_on=True)
    starts = []
    monkeypatch.setattr(w, "_start_glint", lambda: starts.append(1))
    # first populate -> a champion CHANGE -> the glint sweep is started ONCE.
    w.set_data(m)
    assert w._last_champion == SONNET
    assert len(starts) == 1
    # re-arrive with the SAME champion (a fresh equal object) -> NO re-start.
    m2 = build_model_of_week(_one_bucket_rows())
    w.set_data(m2)
    assert len(starts) == 1                    # still ONE — the gate held
    assert w.get_glint() == pytest.approx(1.0)  # parked at rest, no re-sweep
    # a DIFFERENT champion -> the gate opens and the sweep starts again.
    rows3 = [_row("2026-06-21", DEEPSEEK, 9.0, 9_000_000, 90)]
    w.set_data(build_model_of_week(rows3))
    assert len(starts) == 2


def test_widget_glint_property_distinct_no_move(qapp):
    # `glint` is a DISTINCT Property (NOT pos/size/geometry); setting it changes
    # only the sheen, never the widget geometry (the no-move regression).
    m = build_model_of_week(_one_bucket_rows())
    w = _belt(qapp, m)
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_glint(0.5)
    assert w.get_glint() == pytest.approx(0.5)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before


def test_widget_anim_disabled_glint_parks_at_one(qapp):
    # reduce-motion -> glint parked at 1.0 (no running anim).
    m = build_model_of_week(_one_bucket_rows())
    w = _belt(qapp, m, anim_on=False)
    assert w.get_glint() == pytest.approx(1.0)


def test_widget_monogram_fallback_when_no_logo(qapp):
    # No logo store / no cached tile -> the champion gets a painted MONOGRAM disc
    # (first letter), the acceptable fallback (the champion isn't a pinned model).
    m = build_model_of_week(_one_bucket_rows())
    w = _belt(qapp, m, logo_store=None)
    assert w._champion_pixmap() is None        # no pixmap -> monogram path
    assert w._monogram_letter() == "A"         # 'anthropic' -> 'A'
    _grab(w)


def test_widget_logo_used_when_pixmap_available(qapp):
    # When the logo store DOES have a tile for the provider, the belt uses it
    # (the pixmap path) instead of the monogram.
    from PySide6.QtGui import QPixmap

    class _FakeStore:
        def request(self, slug, url):
            pass
        def tile_path(self, slug):
            return "C:/fake/anthropic.png" if slug == "anthropic" else None

    # Monkeypatch QPixmap loading so we don't need a real file on disk.
    m = build_model_of_week(_one_bucket_rows())
    w = _belt(qapp, m, logo_store=_FakeStore())
    # Inject a non-null pixmap + matching slug as if the tile file had loaded.
    px = QPixmap(40, 40)
    px.fill()
    w._logo_pixmap_cache = px
    w._logo_slug_cached = "anthropic"
    assert w._champion_pixmap() is not None
    _grab(w)


def test_widget_sizehint_measure_once(qapp):
    # The height is font-metric-driven and stable (one measure pass feeds paint +
    # sizeHint). The locked + populated heights MATCH so the section never jumps.
    m = build_model_of_week(_one_bucket_rows())
    w = _belt(qapp, m)
    h_pop = w.sizeHint().height()
    assert w.height() == h_pop
    w.set_locked()
    assert w.sizeHint().height() == h_pop      # no jump locked vs populated


def test_widget_click_emits_week_clicked(qapp):
    # Clicking the belt emits week_clicked(anchor_y_global) -> the dossier path.
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    m = build_model_of_week(_one_bucket_rows())
    w = _belt(qapp, m)
    fired = []
    w.week_clicked.connect(lambda y: fired.append(y))
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(20, 40),
                     QPointF(20, 40), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert len(fired) == 1


def test_belt_dossier_html_escaped_and_complete(qapp):
    # The dossier HTML carries the full model id + the Week-1 grace line; the
    # exact week spend/tokens/requests/date + runner-up are QPainter-drawn into
    # the embedded pixmap (injection-safe), so they live in the STRIP rows.
    from widgets import build_week_dossier_html, WeekDossierStrip
    m = build_model_of_week(_one_bucket_rows())
    html_str = build_week_dossier_html(m)
    assert SONNET in html_str                          # full model id (escaped)
    assert "<img src='data:image/png;base64," in html_str   # the painted strip
    assert "Week 1" in html_str                        # the inaugural-belt line
    # the exact facts are in the strip's measured rows.
    rows = {k: v for k, v in WeekDossierStrip(m)._rows}
    assert rows["week"] == "Week of Jun 21 2026"
    assert rows["spend"] == "$4.45"
    assert rows["tokens"] == "6,695,148"
    assert rows["requests"] == "102"
    assert "Claude 4.5 Haiku" in rows["runner-up"]      # runner-up trace


def test_belt_dossier_html_escapes_injection(qapp):
    # A hostile model id/name can't break out of the HTML wrapper (html.escape).
    from widgets import build_week_dossier_html
    evil = ModelOfWeek(champion_id="x/<script>alert(1)</script>",
                       champion_name="<b>pwn</b>", provider="x",
                       share=1.0, week_spend=1.0, week_tokens=10, week_requests=1,
                       bucket_label="2026-06-21", date_label="Week of Jun 21 2026",
                       week_count=1)
    html_str = build_week_dossier_html(evil)
    assert "<script>" not in html_str
    assert "&lt;script&gt;" in html_str
    assert "<b>pwn</b>" not in html_str


def _grab(w):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    img = QImage(w.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    w.render(p, QPoint(0, 0))
    p.end()
    return img
