"""Deterministic tests for Wave 2 #14 — THE HOURGLASS (budget burn-down).

Two layers, both MEASURED (the deterministic-validation discipline — no
eyeballing, no flaky clicking):

  1. PURE — no Qt:
     (a) budget_geometry(spent, budget, elapsed_frac) -> (top_h, bottom_h,
         pace_y, over_pace): the spec's 41/50/0.5 case (bottom/28≈0.82,
         top/28≈0.18, pace_y at 0.5 of the should-spent height, over_pace True),
         the budget<=0 guard, and the on-track case.
     (b) build_budget(rows, budget_value, ...) -> a Budget across the THREE
         states (populated weekly / populated credits-fallback / no-budget),
         with the projection math (avg_daily*days_left + spent == $Z) and the
         elapsed_days==0 guard for the young account. NEVER invents a denominator.

  2. THE WIDGET (qapp) — the #14 TEST_PLAN: BudgetHourglass.set_data(a populated
     fixture spent=41/budget=50/elapsed=0.5) -> (a) setFixedHeight ≈84 (no clip);
     (b) row2 text == "82% burned · N days left"; (c) the pinch glow is RED when
     spent_frac>elapsed_frac else accent; (d) set_no_budget renders the dashed
     glass + "Set a budget" (NO fabricated denominator); (e) set_locked renders
     the padlocked empty glass; (f) display_frac is a DISTINCT Property (NOT
     pos/size) + a widget-doesn't-move regression.
"""
import pytest

import api_client as a
from api_client import (
    budget_geometry, build_budget, Budget, build_spend_board, SpendBoard,
    HOURGLASS_BULB_H,
)

BULB = HOURGLASS_BULB_H   # 28.0


def _day(date, usage):
    """A verbatim-shaped QUERY D day row: date__day key (dims=[]) + float usage."""
    return {"date__day": date, "total_usage": usage}


# Live-shaped 7d series (re-verified 2026-06-24): the spike day dominates.
LIVE_ROWS = [
    _day("2026-06-21", 0.055373),
    _day("2026-06-22", 4.367532),
    _day("2026-06-23", 0.033294),
]
LIVE_SPENT = sum(r["total_usage"] for r in LIVE_ROWS)   # ≈ 4.4562


# ===========================================================================
#  PURE — budget_geometry (decision C)
# ===========================================================================
def test_geometry_spec_case_41_50_half():
    # the spec's locked TEST_PLAN case.
    top_h, bottom_h, pace_y, over = budget_geometry(41, 50, 0.5)
    assert bottom_h / BULB == pytest.approx(0.82, abs=1e-6)   # spent_frac
    assert top_h / BULB == pytest.approx(0.18, abs=1e-6)      # remaining_frac
    # pace_y is at 0.5 of the should-spent (bottom) bulb height.
    assert pace_y == pytest.approx(BULB * 0.5)
    assert over is True                                       # 0.82 > 0.5


def test_geometry_top_plus_bottom_is_full_bulb():
    # the inversion is exact: the two fills always sum to one bulb height.
    for spent, budget in [(0, 50), (25, 50), (50, 50), (10, 40)]:
        top_h, bottom_h, _, _ = budget_geometry(spent, budget, 0.5)
        assert top_h + bottom_h == pytest.approx(BULB)


def test_geometry_budget_zero_is_guarded():
    # budget<=0 -> spent_frac 0 (full top bulb, empty bottom), never a ZeroDiv.
    top_h, bottom_h, pace_y, over = budget_geometry(5, 0, 0.5)
    assert bottom_h == 0.0
    assert top_h == pytest.approx(BULB)
    assert over is False
    # negative budget guarded too.
    assert budget_geometry(5, -3, 0.5)[1] == 0.0


def test_geometry_on_track_under_pace_not_over():
    # spent_frac (0.2) BELOW elapsed_frac (0.5) -> NOT over pace (the accent case).
    _, bottom_h, _, over = budget_geometry(10, 50, 0.5)
    assert bottom_h / BULB == pytest.approx(0.2)
    assert over is False


def test_geometry_clamps_overspend_and_elapsed():
    # spent past budget clamps to a full bottom bulb; elapsed>1 clamps to full.
    top_h, bottom_h, pace_y, over = budget_geometry(80, 50, 1.5)
    assert bottom_h == pytest.approx(BULB)
    assert top_h == 0.0
    assert pace_y == pytest.approx(BULB)     # elapsed_frac clamped to 1.0


# ===========================================================================
#  PURE — build_budget (decision A/D/E) + the projection math
# ===========================================================================
def test_build_budget_weekly_populated():
    # source="weekly": Σ the day rows; the same spend reads as LOW against a big
    # denominator (the WILD point a bar can't make).
    b = build_budget(LIVE_ROWS, 50.0, "2026-06-17", "2026-06-24",
                     source="weekly", period_days=7)
    assert b.source == "weekly"
    assert b.has_budget is True
    assert b.spent == pytest.approx(LIVE_SPENT, abs=1e-6)
    assert b.budget == 50.0
    assert b.spent_frac == pytest.approx(LIVE_SPENT / 50.0)
    # 3 distinct day buckets returned -> elapsed_days 3, days_left 4.
    assert b.elapsed_days == 3
    assert b.days_left == 4
    assert b.elapsed_frac == pytest.approx(3 / 7)
    # NOT over pace (8.9% spent < 42.9% elapsed) -> the calm accent state.
    assert b.over_pace is False


def test_build_budget_credits_fallback_uses_live_burned():
    # source="credits": budget=total_credits, the live total_usage is the
    # authoritative burned $ (the day-rows still feed the series). The live
    # ~45%-of-$10 burn-down IS ahead of pace on day 3/7.
    b = build_budget(LIVE_ROWS, 10.0, "2026-06-17", "2026-06-24",
                     source="credits", period_days=7, credits_spent=4.456)
    assert b.source == "credits"
    assert b.has_budget is True
    assert b.budget == 10.0
    assert b.spent == pytest.approx(4.456)
    assert b.pct_burned == 45               # round(0.4456*100)
    # 44.6% spent > 42.9% elapsed -> AHEAD of pace (the RED pinch case).
    assert b.over_pace is True
    assert b.daily == tuple((r["date__day"], r["total_usage"]) for r in LIVE_ROWS)


def test_build_budget_projection_math():
    # the explicit forecast: avg_daily*days_left + spent == projection.
    b = build_budget(LIVE_ROWS, 10.0, "s", "e", source="credits",
                     period_days=7, credits_spent=4.456)
    avg = 4.456 / 3                          # spent / elapsed_days
    assert b.avg_daily == pytest.approx(avg)
    assert b.projection == pytest.approx(4.456 + avg * 4)
    # this account is forecast to OVERSHOOT $10 -> the projection row goes RED.
    assert b.over_projection is True


def test_build_budget_elapsed_days_zero_guarded():
    # the young-account guard: NO day rows -> elapsed_days 0, NO ZeroDiv, a tidy
    # zeroed projection (never NaN/inf).
    b = build_budget([], 10.0, "s", "e", source="credits", period_days=7,
                     credits_spent=0.0)
    assert b.elapsed_days == 0
    assert b.avg_daily == 0.0
    assert b.projection == 0.0
    assert b.elapsed_frac == 0.0
    assert b.over_pace is False


def test_build_budget_no_budget_never_fabricates():
    # decision A: no denominator (weekly 0, source none) -> source="none", a real
    # spent figure from the rows but ZERO budget — NEVER an invented number.
    b = build_budget(LIVE_ROWS, 0.0, "s", "e", source="none", period_days=7)
    assert b.source == "none"
    assert b.has_budget is False
    assert b.budget == 0.0
    assert b.spent == pytest.approx(LIVE_SPENT, abs=1e-6)   # spent is a fact
    # weekly_budget<=0 is forced to the no-budget state even if source says weekly.
    b2 = build_budget(LIVE_ROWS, 0.0, "s", "e", source="weekly", period_days=7)
    assert b2.has_budget is False
    assert b2.source == "none"


def test_build_budget_never_raises_on_garbage():
    b = build_budget([{"garbage": 1}, "not-a-dict", None], 50.0, "s", "e",
                     source="weekly")
    assert isinstance(b, Budget)
    assert b.spent == 0.0
    assert b.elapsed_days == 0


def test_build_budget_detects_created_at_bucket_key():
    # the bucket key flips to created_at__day in some shapes; _bucket_key detects
    # it so the series + elapsed_days are still correct.
    rows = [{"created_at__day": "2026-06-22", "total_usage": 4.0},
            {"created_at__day": "2026-06-23", "total_usage": 1.0}]
    b = build_budget(rows, 50.0, "s", "e", source="weekly", period_days=7)
    assert b.spent == pytest.approx(5.0)
    assert b.elapsed_days == 2


def test_spend_board_can_carry_budget():
    # the board slot accepts a Budget (dataclasses.replace wiring in get_spend_board).
    import dataclasses
    rows_q_a = [
        {"date__day": "2026-06-22", "model": "anthropic/claude-4.6-sonnet",
         "total_usage": 4.0, "request_count": "95", "tokens_prompt": "1000",
         "tokens_completion": "100", "reasoning_tokens": "0",
         "cached_tokens": "0", "usage_cache": 0.0, "cache_hit_rate": 0.0},
    ]
    board = build_spend_board(rows_q_a, granularity="day", start="s", end="e")
    budget = build_budget(LIVE_ROWS, 50.0, "s", "e", source="weekly")
    board = dataclasses.replace(board, budget=budget)
    assert isinstance(board, SpendBoard)
    assert board.budget is budget
    assert board.budget.has_budget is True


# ===========================================================================
#  THE BUDGET HOURGLASS widget (qapp) — implements the #14 TEST_PLAN
# ===========================================================================
def _fixture_budget(spent=41.0, budget=50.0, elapsed_frac=0.5, days_left=3,
                    elapsed_days=4, source="weekly"):
    """A populated Budget with the spec's 41/50/0.5 numbers (spent_frac 0.82 >
    elapsed 0.5 -> over pace). projection picked so over_projection is exercised
    separately."""
    avg = spent / elapsed_days if elapsed_days else 0.0
    return Budget(
        spent=spent, budget=budget,
        spent_frac=max(0.0, min(1.0, spent / budget)),
        elapsed_frac=elapsed_frac, days_left=days_left, elapsed_days=elapsed_days,
        projection=spent + avg * days_left, avg_daily=avg,
        over_pace=(spent / budget) > elapsed_frac, source=source,
        period_days=7, daily=(("2026-06-21", 5.0), ("2026-06-22", 30.0),
                              ("2026-06-23", 6.0)),
    )


def _hourglass(qapp, budget=None, width=300, anim_on=False):
    from widgets import BudgetHourglass
    import anim
    anim.set_enabled(anim_on)
    w = BudgetHourglass()
    w.resize(width, 90)
    if budget is not None:
        w.set_data(budget)
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


def test_hourglass_fixed_height_about_84(qapp):
    # TEST_PLAN a: setFixedHeight ≈ 84 (no clip).
    from widgets import BudgetHourglass
    w = _hourglass(qapp, _fixture_budget())
    h = w.height()
    assert 78 <= h <= 90, f"height {h} outside the ~84 band"
    assert h == BudgetHourglass._fixed_height()
    img = _grab(w)
    assert img.height() == h          # no clip


def test_hourglass_row2_text_is_pct_burned_days_left(qapp):
    # TEST_PLAN b: row2 == "82% burned · N days left" for the 41/50 fixture.
    w = _hourglass(qapp, _fixture_budget(spent=41.0, budget=50.0, days_left=3))
    assert w._row2 == "82% burned · 3 days left"
    # row1 + row3 are also the honest formatted figures.
    assert w._row1 == "$41.00 / $50"
    assert w._row3.startswith("on pace for $")


def test_hourglass_pinch_red_when_over_pace_else_accent(qapp):
    # TEST_PLAN c: pinch glow is RED when spent_frac>elapsed_frac, else accent.
    from theme import Colors
    import theme_controller
    # OVER pace: spent 0.82 > elapsed 0.5.
    over = _fixture_budget(spent=41.0, budget=50.0, elapsed_frac=0.5)
    assert over.over_pace is True
    # the widget chooses RED for the pinch in this state — assert via the data
    # contract the paint reads (over_pace) AND the dossier accent helper.
    from widgets import budget_accent_hex
    assert budget_accent_hex(over) == Colors.RED.name()
    # UNDER pace: spent 0.2 < elapsed 0.5 -> accent (NOT red).
    under = _fixture_budget(spent=10.0, budget=50.0, elapsed_frac=0.5)
    assert under.over_pace is False
    assert budget_accent_hex(under) == theme_controller.accent().name()


def test_hourglass_over_pace_paints_without_clip(qapp):
    # the over-pace populated path paints fully (RED crest + "▲ ahead of pace").
    w = _hourglass(qapp, _fixture_budget(spent=41.0, budget=50.0))
    img = _grab(w)
    assert img.width() > 0 and img.height() == w.height()
    assert w._budget.over_pace is True


def test_hourglass_no_budget_state(qapp):
    # TEST_PLAN d: set_no_budget renders the dashed glass + "Set a budget", NO
    # fabricated denominator.
    w = _hourglass(qapp)
    w.set_no_budget()
    assert w._no_budget is True
    assert w._locked is False
    assert w._caption == "Set a budget"
    # NO denominator rows -> the populated rows stay empty (no fake $X/$Y).
    assert w._row1 == "" and w._row2 == "" and w._row3 == ""
    _grab(w)                          # paints dashed glass + pill without error


def test_hourglass_no_budget_via_set_data_sentinel(qapp):
    # a Budget("none") handed to set_data routes to the no-budget state (decision
    # A) — a credit balance is never silently shown as a budget.
    w = _hourglass(qapp)
    none_budget = build_budget(LIVE_ROWS, 0.0, "s", "e", source="none")
    w.set_data(none_budget)
    assert w._no_budget is True
    assert w._row1 == ""              # the real spent is NOT presented as /budget
    assert w._caption == "Set a budget"


def test_hourglass_locked_state(qapp):
    # TEST_PLAN e: set_locked renders the padlocked empty glass + the unlock copy.
    from widgets import SPEND_UNLOCK_BASE
    w = _hourglass(qapp)
    w.set_locked()
    assert w._locked is True
    assert w._budget is None
    assert "track a budget" in w._caption.lower()
    assert w._caption.startswith(SPEND_UNLOCK_BASE)
    assert w.height() == w._fixed_height()   # height stays (section won't jump)
    _grab(w)                          # paints padlock + outline glass without error


def test_hourglass_display_frac_is_distinct_property_no_move(qapp):
    # TEST_PLAN f: display_frac is a DISTINCT Property (NOT pos/size/geometry);
    # setting it changes only the fill, never the geometry (the no-move regression).
    w = _hourglass(qapp, _fixture_budget())
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_display_frac(0.5)
    assert w.get_display_frac() == pytest.approx(0.5)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before


def test_hourglass_reveal_fires_once_then_silent_on_identical(qapp):
    # the one-time reveal: animations ON + visible -> the held anim starts once;
    # a 2nd identical poll (same signature) must NOT re-animate.
    from widgets import BudgetHourglass
    import anim
    anim.set_enabled(True)
    w = BudgetHourglass()
    w.resize(300, 90)
    w.show()                          # isVisible() True -> the reveal runs
    w.set_data(_fixture_budget())
    assert w._reveal_started_count == 1
    assert w._anim is not None
    w.set_data(_fixture_budget())     # identical signature -> silent
    assert w._reveal_started_count == 1


def test_hourglass_anim_disabled_sets_final_directly(qapp):
    # animations OFF: NO running anim, display_frac parked at the target.
    fx = _fixture_budget(spent=41.0, budget=50.0)
    w = _hourglass(qapp, fx, anim_on=False)
    assert w.get_display_frac() == pytest.approx(fx.spent_frac)
    assert w._reveal_started_count == 0


def test_hourglass_set_data_none_keeps_last_good(qapp):
    w = _hourglass(qapp, _fixture_budget())
    sig_before = w._signature
    frac_before = w._target_frac
    w.set_data(None)                  # keep last-good, never blank
    assert w._budget is not None
    assert w._signature == sig_before
    assert w._target_frac == frac_before


def test_hourglass_click_emits_budget_clicked(qapp):
    # a click on a populated glass emits budget_clicked (the popup entry point).
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    w = _hourglass(qapp, _fixture_budget())
    captured = []
    w.budget_clicked.connect(lambda anchor: captured.append(anchor))
    c = QPointF(w.width() / 2, w.height() / 2)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, c, c,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert len(captured) == 1


def test_hourglass_locked_click_is_inert(qapp):
    # the locked glass has no dossier -> a click does NOT emit.
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    w = _hourglass(qapp)
    w.set_locked()
    captured = []
    w.budget_clicked.connect(lambda anchor: captured.append(anchor))
    c = QPointF(w.width() / 2, w.height() / 2)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, c, c,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert captured == []


# ----- the burn-down dossier popup HTML -----
def test_budget_html_populated_has_chart_and_math(qapp):
    from widgets import build_budget_html
    html_str = build_budget_html(_fixture_budget(spent=41.0, budget=50.0))
    assert "data:image/png;base64," in html_str   # the column-chart pixmap
    assert "burned" in html_str
    # the projection math line is rendered into the pixmap (QPainter), and the
    # header carries the forecast $.
    assert "$" in html_str


def test_budget_html_no_budget_card_never_fabricates(qapp):
    # decision E: a no-denominator budget -> a tidy "No budget set" card, no $Y.
    from widgets import build_budget_html
    none_budget = build_budget(LIVE_ROWS, 0.0, "s", "e", source="none")
    html_str = build_budget_html(none_budget)
    assert "No budget set" in html_str
    assert "weekly_budget" in html_str
    assert "never invents" in html_str.lower()


def test_budget_accent_is_red_only_over_pace(qapp):
    # decision C: RED is scoped STRICTLY to over_pace so it doesn't fight #11/#13.
    from widgets import budget_accent_hex
    from theme import Colors
    import theme_controller
    over = _fixture_budget(spent=41.0, budget=50.0, elapsed_frac=0.5)
    under = _fixture_budget(spent=5.0, budget=50.0, elapsed_frac=0.5)
    assert budget_accent_hex(over) == Colors.RED.name()
    assert budget_accent_hex(under) != Colors.RED.name()
    assert budget_accent_hex(under) == theme_controller.accent().name()
    # None (no budget) -> the panel accent, never red.
    assert budget_accent_hex(None) == theme_controller.accent().name()
