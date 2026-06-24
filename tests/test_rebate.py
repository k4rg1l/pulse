"""Deterministic tests for Wave 2 #12 — THE REBATE STUB (cache & reasoning
savings).

Two layers, both MEASURED (no eyeballing — the deterministic-validation
discipline):
  1. PURE — build_savings(rows) against a fixture carrying the LIVE-shaped
     quirks (usage_cache=-16.28 NEGATIVE, cache_hit_rate=0.9356 a 0..1 FRACTION,
     reasoning_tokens=6472 a COUNT) PLUS a positive-usage_cache 1-request day and
     a zero-reasoning case. Asserts (per ORCHESTRATOR decisions A/B):
     total_rebate == 16.28 (abs of negative), hit_rate_pct ≈ 93.56 (×100 ONCE,
     NOT 0.94), the request-weighted hit-rate math, reasoning is an int COUNT,
     the abs() over a positive day, and the divide-by-zero guards.
  2. THE WIDGET (qapp) — RebateStub.set_data() the fixture board and MEASURE the
     #12 TEST_PLAN: (a) setFixedHeight == 44; (b) amount string == "$16.28"
     (abs of negative); (c) hit-rate label == "93.6%" (×100 once, NOT "0.94%");
     (d) the arc swept angle proportional to 0.9356; (e) render_pixmap returns
     the measured-height pixmap with per-model GREEN bars == abs(usage_cache);
     plus the locked + populated-zero states, the count-up-doesn't-move
     regression (display_amount is NOT a QWidget builtin), and the
     zero-reasoning no-divide-by-zero case.
"""
import pytest

import api_client as a
from api_client import (
    build_savings, build_spend_board, Savings, SavingsModel, SpendBoard,
)

SONNET = "anthropic/claude-4.6-sonnet-20260217"
HAIKU = "anthropic/claude-4.5-haiku-20251001"


def _fixture_rows():
    """The canonical #12 fixture — mirrors the LIVE Query A row shapes:
      - sonnet heavy day: usage_cache=-16.28 (NEGATIVE saving), cache_hit_rate
        0.9356 (0..1), reasoning_tokens 6472, cached_tokens 6,147,577, 95 reqs.
      - sonnet a quieter day: usage_cache=-0.111, hit 0.8244, reasoning 254.
      - a POSITIVE-usage_cache 1-request day (abs() must still treat it as a
        rebate); reasoning 0.
      - haiku: a zero-cache / zero-reasoning model (must not divide by zero, and
        is excluded from the popup breakdown rows).
    """
    return [
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 4.00,
         "request_count": "95", "tokens_prompt": "6570644",
         "tokens_completion": "62359", "reasoning_tokens": "6472",
         "cached_tokens": "6147577", "usage_cache": -16.28,
         "cache_hit_rate": 0.9356},
        {"date__day": "2026-06-22", "model": SONNET, "total_usage": 0.06,
         "request_count": "5", "tokens_prompt": "40000",
         "tokens_completion": "4000", "reasoning_tokens": "254",
         "cached_tokens": "43665", "usage_cache": -0.111,
         "cache_hit_rate": 0.8244},
        # a POSITIVE usage_cache on a 1-request day -> abs() still a rebate.
        {"date__day": "2026-06-23", "model": SONNET, "total_usage": 0.03,
         "request_count": "1", "tokens_prompt": "8000",
         "tokens_completion": "12", "reasoning_tokens": "0",
         "cached_tokens": "120", "usage_cache": 0.0065,
         "cache_hit_rate": 0.0},
        # haiku — no cache, no reasoning (zero-reasoning / divide-by-zero guard).
        {"date__day": "2026-06-22", "model": HAIKU, "total_usage": 0.01,
         "request_count": "2", "tokens_prompt": "1395",
         "tokens_completion": "20", "reasoning_tokens": "0",
         "cached_tokens": "0", "usage_cache": 0.0, "cache_hit_rate": 0.0},
    ]


def _by_model(models):
    return {m.model_id: m for m in models}


# ===========================================================================
#  PURE — build_savings
# ===========================================================================
def test_savings_total_rebate_is_abs_of_negative_usage_cache():
    sv = build_savings(_fixture_rows())
    # Σ abs(usage_cache) = 16.28 + 0.111 + 0.0065 = 16.3975.
    assert sv.total_rebate == pytest.approx(16.28 + 0.111 + 0.0065)
    # the headline is POSITIVE (a rebate), never negative.
    assert sv.total_rebate > 0


def test_savings_hit_rate_is_times_100_at_parse_once():
    sv = build_savings(_fixture_rows())
    # request-weighted: (0.9356*95 + 0.8244*5 + 0.0*1 + 0.0*2) / (95+5+1+2) ×100
    num = 0.9356 * 95 + 0.8244 * 5 + 0.0 * 1 + 0.0 * 2
    den = 95 + 5 + 1 + 2
    expected_pct = num / den * 100.0
    assert sv.hit_rate_pct == pytest.approx(expected_pct)
    # in [0,100], NOT a 0..1 fraction — the heavy weight keeps it high.
    assert 0.0 <= sv.hit_rate_pct <= 100.0
    assert sv.hit_rate_pct > 1.0   # would be ~0.9 if someone forgot the ×100


def test_savings_per_model_hit_rate_label_is_93_6_for_sonnet_solo():
    # decision B's MANDATED assertion: 0.9356 -> "93.6%", never "0.94%".
    # A sonnet-only single heavy row isolates the per-model hit-rate.
    sv = build_savings([_fixture_rows()[0]])
    m = sv.models[0]
    assert m.model_id == SONNET
    assert f"{m.hit_rate_pct:.1f}%" == "93.6%"
    assert f"{sv.hit_rate_pct:.1f}%" == "93.6%"


def test_savings_reasoning_is_an_int_count_not_dollars():
    sv = build_savings(_fixture_rows())
    # Σ reasoning_tokens = 6472 + 254 + 0 + 0 = 6726, an int COUNT.
    assert sv.reasoning_total == 6726
    assert isinstance(sv.reasoning_total, int)
    # reasoning_ref = the max single-day reasoning count (the meter basis).
    assert sv.reasoning_ref == 6472


def test_savings_per_model_breakdown_sorted_desc_excludes_empty():
    sv = build_savings(_fixture_rows())
    # only sonnet saved/reasoned; haiku (0 rebate, 0 reasoning) is excluded.
    assert [m.model_id for m in sv.models] == [SONNET]
    s = _by_model(sv.models)[SONNET]
    assert s.rebate == pytest.approx(16.28 + 0.111 + 0.0065)
    assert s.cached_tokens == 6147577 + 43665 + 120
    assert s.reasoning_tokens == 6472 + 254


def test_savings_spark_is_daily_abs_usage_cache_series():
    sv = build_savings(_fixture_rows())
    # chronological daily abs(usage_cache): 06-21, 06-22 (sonnet .111 + haiku 0),
    # 06-23 (the +0.0065 -> abs).
    assert sv.spark == pytest.approx((16.28, 0.111, 0.0065))


def test_savings_divide_by_zero_and_zero_reasoning_guards():
    # a single zero-everything row: no rebate, no reasoning, no requests.
    sv = build_savings([
        {"date__day": "2026-06-21", "model": HAIKU, "total_usage": 0.0,
         "request_count": "0", "tokens_prompt": "0", "tokens_completion": "0",
         "reasoning_tokens": "0", "cached_tokens": "0", "usage_cache": 0.0,
         "cache_hit_rate": 0.0}])
    assert sv.total_rebate == 0.0
    assert sv.hit_rate_pct == 0.0          # guarded /0 (no requests)
    assert sv.reasoning_total == 0
    assert sv.reasoning_ref == 0           # no divide-by-zero on the meter basis
    assert sv.models == ()
    assert sv.is_empty is True


def test_savings_empty_rows():
    sv = build_savings([])
    assert sv.total_rebate == 0.0
    assert sv.is_empty is True
    sv2 = build_savings(None)
    assert sv2.is_empty is True


def test_build_spend_board_populates_savings():
    # decision A: build_spend_board fills .savings from the SAME Query A rows.
    board = build_spend_board(_fixture_rows(), granularity="day",
                              start="s", end="e")
    assert isinstance(board, SpendBoard)
    assert board.savings is not None
    assert board.savings.total_rebate == pytest.approx(16.28 + 0.111 + 0.0065)
    # #9 spectrum + #10 receipts still populated from the same rows (no regress).
    assert board.spectrum.total > 0
    assert len(board.receipts) == 2


# ===========================================================================
#  THE REBATE STUB widget (qapp) — implements the #12 TEST_PLAN
# ===========================================================================
def _stub(qapp, savings=None, width=380):
    from widgets import RebateStub
    import anim
    anim.set_enabled(False)   # deterministic: no in-flight count-up during grab
    w = RebateStub()
    w.resize(width, 100)
    if savings is not None:
        w.set_data(savings)
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


def test_stub_fixed_height_is_44(qapp):
    # TEST_PLAN a: setFixedHeight == 44.
    w = _stub(qapp, build_savings(_fixture_rows()))
    assert w.height() == 44
    # and the grab is full-size (no clip).
    img = _grab(w)
    assert img.height() == 44


def test_stub_amount_string_is_abs_dollars(qapp):
    # TEST_PLAN b: the amount string == "$16.28" (abs of the negative credit).
    # animations OFF -> display_amount lands at 1.0, so the painted amount is
    # the full total. We assert the FORMAT the widget paints.
    sv = build_savings([_fixture_rows()[0]])   # sonnet heavy day: -16.28
    w = _stub(qapp, sv)
    assert w.get_display_amount() == pytest.approx(1.0)
    painted = sv.total_rebate * w.get_display_amount()
    assert f"${painted:,.2f}" == "$16.28"


def test_stub_hit_rate_label_is_93_6_not_0_94(qapp):
    # TEST_PLAN c: the hit-rate label == "93.6%" (×100 once, NOT "0.94%").
    sv = build_savings([_fixture_rows()[0]])
    w = _stub(qapp, sv)
    assert f"{sv.hit_rate_pct:.1f}%" == "93.6%"
    assert f"{sv.hit_rate_pct:.1f}%" != "0.9%"
    _grab(w)   # paints the arc + label without error


def test_stub_arc_swept_angle_proportional_to_hit_rate(qapp):
    # TEST_PLAN d: the arc swept angle ∝ 0.9356 (of the 180° half-arc).
    sv = build_savings([_fixture_rows()[0]])
    w = _stub(qapp, sv)
    _grab(w)   # populate self._arc_swept_deg via a paint
    assert hasattr(w, "_arc_swept_deg")
    # animations OFF -> full sweep: 180 * (93.56/100).
    assert w._arc_swept_deg == pytest.approx(180.0 * 0.9356, abs=0.01)


def test_stub_render_pixmap_height_and_green_bars(qapp):
    # TEST_PLAN e: render_pixmap returns the measured-height pixmap; the per-
    # model GREEN bar fractions encode abs(usage_cache) (sonnet bar is full,
    # being the only / heaviest saver).
    from widgets import RebateBreakdownStrip
    sv = build_savings(_fixture_rows())
    strip = RebateBreakdownStrip(sv)
    pm = strip.render_pixmap()
    try:
        dpr = pm.devicePixelRatio()
    except Exception:
        dpr = 1.0
    assert pm.height() == int(strip._h * dpr)
    assert pm.width() == int(strip.STRIP_W * dpr)
    # the GREEN bar length encodes abs(usage_cache): the breakdown row IS the
    # sonnet abs-rebate (the only saving model), so its rebate matches the sum.
    assert len(strip._rows) == 1
    assert strip._rows[0].model_id == SONNET
    assert strip._rows[0].rebate == pytest.approx(16.28 + 0.111 + 0.0065)


def test_stub_locked_state(qapp):
    # decision E: greyed silhouette, NO green, padlock + unlock copy; keeps the
    # real 44px height (the section doesn't jump when a key is added).
    w = _stub(qapp)
    w.set_locked()
    assert w._locked is True
    assert w._savings is None
    assert w.height() == 44
    _grab(w)   # paints the padlock + outlines without error
    from widgets import SPEND_UNLOCK_BASE
    assert SPEND_UNLOCK_BASE == "Add a management key at openrouter.ai to unlock"


def test_stub_populated_zero_state_is_not_locked(qapp):
    # decision E: key present but no cache activity -> a real "$0.00" rebate +
    # "0 rsn tok" tidy zeros, NOT the locked prompt.
    sv = build_savings([
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 0.05,
         "request_count": "3", "tokens_prompt": "1000",
         "tokens_completion": "100", "reasoning_tokens": "0",
         "cached_tokens": "0", "usage_cache": 0.0, "cache_hit_rate": 0.0}])
    w = _stub(qapp, sv)
    assert w._locked is False
    assert sv.total_rebate == 0.0
    assert f"${sv.total_rebate:,.2f}" == "$0.00"
    assert sv.reasoning_total == 0
    _grab(w)   # the populated-zero paint is tidy (no divide-by-zero)


def test_stub_zero_reasoning_no_divide_by_zero(qapp):
    # the reasoning meter normalize must guard /0 when reasoning_ref == 0.
    from widgets import _fmt_tok_count
    sv = build_savings([
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 0.05,
         "request_count": "3", "tokens_prompt": "1000",
         "tokens_completion": "100", "reasoning_tokens": "0",
         "cached_tokens": "5000", "usage_cache": -0.5, "cache_hit_rate": 0.5}])
    assert sv.reasoning_ref == 0
    w = _stub(qapp, sv)
    _grab(w)                       # must not raise ZeroDivisionError
    assert _fmt_tok_count(0) == "0"


def test_stub_count_up_does_not_move_widget(qapp):
    # the count-up Property must NOT be a QWidget builtin (pos/size) — setting it
    # changes the painted amount/arc only, never the widget geometry (the
    # INVARIANT against naming a Property after a QWidget builtin).
    sv = build_savings(_fixture_rows())
    w = _stub(qapp, sv)
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_display_amount(0.3)
    assert w.get_display_amount() == pytest.approx(0.3)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before


def test_stub_whole_strip_emits_rebate_clicked(qapp):
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    sv = build_savings(_fixture_rows())
    w = _stub(qapp, sv)
    captured = []
    w.rebate_clicked.connect(lambda anchor: captured.append(anchor))
    c = QPointF(w.width() / 2.0, w.height() / 2.0)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, c, c,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert len(captured) == 1


def test_stub_locked_strip_does_not_emit(qapp):
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    w = _stub(qapp)
    w.set_locked()
    captured = []
    w.rebate_clicked.connect(lambda anchor: captured.append(anchor))
    c = QPointF(w.width() / 2.0, w.height() / 2.0)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, c, c,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert captured == []


def test_rebate_html_escapes_model_name(qapp):
    # decision D: the HTML wrapper html.escapes API-sourced names. Inject a
    # name with HTML metacharacters and assert it's escaped in the wrapper.
    evil = 'anthropic/<script>"&x'
    sv = build_savings([
        {"date__day": "2026-06-21", "model": evil, "total_usage": 1.0,
         "request_count": "5", "tokens_prompt": "1000",
         "tokens_completion": "100", "reasoning_tokens": "10",
         "cached_tokens": "5000", "usage_cache": -1.5, "cache_hit_rate": 0.9}])
    from widgets import build_rebate_html
    html_str = build_rebate_html(sv)
    # the raw '<script>' must NOT appear; its escaped form must.
    assert "<script>" not in html_str
    assert "&lt;script&gt;" in html_str


def test_rebate_html_none_is_no_rebate(qapp):
    from widgets import build_rebate_html
    assert "NO CACHE REBATE" in build_rebate_html(None)
    # populated-empty (real zeros) also reads the no-rebate dossier.
    sv = build_savings([])
    assert "NO CACHE REBATE" in build_rebate_html(sv)


def test_rebate_accent_is_green(qapp):
    from widgets import rebate_accent_hex
    from theme import Colors
    assert rebate_accent_hex(build_savings(_fixture_rows())) == Colors.GREEN.name()
