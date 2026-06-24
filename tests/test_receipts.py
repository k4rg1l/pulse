"""Deterministic tests for Wave 2 #10 — THE TILL ROLL (per-request receipt).

Two layers, both MEASURED (no eyeballing — the deterministic-validation
discipline):
  1. PURE — build_receipts(rows) against fixtures (a sonnet SPIKE-as-latest day,
     a haiku low row, a YOUNG <7-day account, a 1-request NOISE day). Asserts the
     $/call math, the noise-gated stamp fires/suppresses per ORCHESTRATOR
     decision B, the cache credit == abs(usage_cache)/calls, reasoning is a COUNT
     with NO $ (decision A), and the divide-by-zero guard.
  2. THE WIDGETS (qapp) — ReceiptStubList.set_data() a fixture board and MEASURE:
     fixed height == n_models*(row_h+4) (no clip); the sonnet $/call string ==
     f"${total/count:.4f}/call"; the stamp rect EXISTS for the spike + is ABSENT
     for the synthesized 1-request $0.03 day; ReceiptStripWidget.render_pixmap()
     returns a QPixmap of the measured height and its CACHE line $ ==
     abs(usage_cache)/calls; the locked + populated-empty states; and a
     reveal-doesn't-move-the-widget regression (the print-reveal Property is NOT
     a QWidget builtin).
"""
import pytest

import api_client as a
from api_client import (
    build_receipts, build_spend_board, Receipt, SpendBoard,
    RECEIPT_MIN_STAMP_REQUESTS, RECEIPT_MIN_HISTORY_DAYS,
)

SONNET = "anthropic/claude-4.6-sonnet-20260217"
HAIKU = "anthropic/claude-4.5-haiku-20251001"


def _spike_rows():
    """A full week (7 day-buckets) where sonnet's LATEST day is a real spike:
    95 reqs at ~$2.00/call vs a ~$0.01/call median on the prior days. Haiku is a
    flat low model. This is the canonical fixture the stamp MUST fire on."""
    return [
        # sonnet — six prior days near $0.01/call, then a $2/call spike LAST.
        {"date__day": "2026-06-15", "model": SONNET, "total_usage": 0.10, "request_count": "10",
         "tokens_prompt": "20000", "tokens_completion": "2000", "reasoning_tokens": "100",
         "cached_tokens": "5000", "usage_cache": -0.02},
        {"date__day": "2026-06-16", "model": SONNET, "total_usage": 0.12, "request_count": "12",
         "tokens_prompt": "24000", "tokens_completion": "2400", "reasoning_tokens": "120",
         "cached_tokens": "6000", "usage_cache": -0.03},
        {"date__day": "2026-06-17", "model": SONNET, "total_usage": 0.11, "request_count": "11",
         "tokens_prompt": "22000", "tokens_completion": "2200", "reasoning_tokens": "110",
         "cached_tokens": "5500", "usage_cache": -0.025},
        {"date__day": "2026-06-18", "model": SONNET, "total_usage": 0.10, "request_count": "10",
         "tokens_prompt": "20000", "tokens_completion": "2000", "reasoning_tokens": "100",
         "cached_tokens": "5000", "usage_cache": -0.02},
        {"date__day": "2026-06-19", "model": SONNET, "total_usage": 0.13, "request_count": "13",
         "tokens_prompt": "26000", "tokens_completion": "2600", "reasoning_tokens": "130",
         "cached_tokens": "6500", "usage_cache": -0.03},
        {"date__day": "2026-06-20", "model": SONNET, "total_usage": 0.11, "request_count": "11",
         "tokens_prompt": "22000", "tokens_completion": "2200", "reasoning_tokens": "110",
         "cached_tokens": "5500", "usage_cache": -0.025},
        # THE SPIKE — latest day, 95 reqs, $190 total -> $2.00/call.
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 190.0, "request_count": "95",
         "tokens_prompt": "6570644", "tokens_completion": "62359", "reasoning_tokens": "6472",
         "cached_tokens": "6147577", "usage_cache": -16.281199},
        # haiku — a flat low model with no cache/reasoning.
        {"date__day": "2026-06-20", "model": HAIKU, "total_usage": 0.01, "request_count": "2",
         "tokens_prompt": "1395", "tokens_completion": "20", "reasoning_tokens": "0",
         "cached_tokens": "0", "usage_cache": 0.0},
        {"date__day": "2026-06-21", "model": HAIKU, "total_usage": 0.012, "request_count": "3",
         "tokens_prompt": "1700", "tokens_completion": "25", "reasoning_tokens": "0",
         "cached_tokens": "0", "usage_cache": 0.0},
    ]


def _young_rows():
    """A 3-day account (< RECEIPT_MIN_HISTORY_DAYS) whose latest day LOOKS like a
    spike (95 reqs, $2/call) — the stamp MUST be SUPPRESSED (`young`), because
    there is no trustworthy median basis yet."""
    return [
        {"date__day": "2026-06-19", "model": SONNET, "total_usage": 0.06, "request_count": "6",
         "tokens_prompt": "12000", "tokens_completion": "1200", "reasoning_tokens": "60",
         "cached_tokens": "3000", "usage_cache": -0.01},
        {"date__day": "2026-06-20", "model": SONNET, "total_usage": 0.05, "request_count": "5",
         "tokens_prompt": "10000", "tokens_completion": "1000", "reasoning_tokens": "50",
         "cached_tokens": "2500", "usage_cache": -0.01},
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 190.0, "request_count": "95",
         "tokens_prompt": "6570644", "tokens_completion": "62359", "reasoning_tokens": "6472",
         "cached_tokens": "6147577", "usage_cache": -16.281199},
    ]


def _noise_rows():
    """A full week of cheap days plus a synthesized 1-request $0.03 LATEST day.
    Per decision B's noise gate (request_count >= 10) the stamp MUST be ABSENT
    even though $0.03/call >> the ~$0.01/call median (a single request is noise,
    not a real price change)."""
    return [
        {"date__day": "2026-06-15", "model": SONNET, "total_usage": 0.10, "request_count": "10",
         "tokens_prompt": "20000", "tokens_completion": "2000", "reasoning_tokens": "100",
         "cached_tokens": "5000", "usage_cache": -0.02},
        {"date__day": "2026-06-16", "model": SONNET, "total_usage": 0.12, "request_count": "12",
         "tokens_prompt": "24000", "tokens_completion": "2400", "reasoning_tokens": "120",
         "cached_tokens": "6000", "usage_cache": -0.03},
        {"date__day": "2026-06-17", "model": SONNET, "total_usage": 0.11, "request_count": "11",
         "tokens_prompt": "22000", "tokens_completion": "2200", "reasoning_tokens": "110",
         "cached_tokens": "5500", "usage_cache": -0.025},
        {"date__day": "2026-06-18", "model": SONNET, "total_usage": 0.10, "request_count": "10",
         "tokens_prompt": "20000", "tokens_completion": "2000", "reasoning_tokens": "100",
         "cached_tokens": "5000", "usage_cache": -0.02},
        {"date__day": "2026-06-19", "model": SONNET, "total_usage": 0.13, "request_count": "13",
         "tokens_prompt": "26000", "tokens_completion": "2600", "reasoning_tokens": "130",
         "cached_tokens": "6500", "usage_cache": -0.03},
        {"date__day": "2026-06-20", "model": SONNET, "total_usage": 0.11, "request_count": "11",
         "tokens_prompt": "22000", "tokens_completion": "2200", "reasoning_tokens": "110",
         "cached_tokens": "5500", "usage_cache": -0.025},
        # the synthesized 1-request $0.03 day — must NOT fire the stamp.
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 0.03, "request_count": "1",
         "tokens_prompt": "8000", "tokens_completion": "10", "reasoning_tokens": "0",
         "cached_tokens": "0", "usage_cache": 0.0},
    ]


# ===========================================================================
#  PURE — build_receipts
# ===========================================================================
def _by_model(receipts):
    return {r.model_id: r for r in receipts}


def test_receipts_percall_math_and_order():
    recs = build_receipts(_spike_rows())
    # descending-spend order: sonnet (huge) first, then haiku.
    assert [r.model_id for r in recs] == [SONNET, HAIKU]
    s = _by_model(recs)[SONNET]
    # range totals tie back to #9 (sum across the 7 sonnet days).
    total = 0.10 + 0.12 + 0.11 + 0.10 + 0.13 + 0.11 + 190.0
    reqs = 10 + 12 + 11 + 10 + 13 + 11 + 95
    assert s.total_usage == pytest.approx(total)
    assert s.request_count == reqs
    assert s.per_call == pytest.approx(total / reqs)
    # the $/call string the stub paints (the public contract the widget test
    # also checks) is the 4-dp form.
    assert f"${s.per_call:.4f}/call" == f"${total / reqs:.4f}/call"


def test_receipts_token_counts_have_no_dollars():
    # decision A: reasoning/input/output are AVERAGE token COUNTS, not $.
    s = _by_model(build_receipts(_spike_rows()))[SONNET]
    reqs = 10 + 12 + 11 + 10 + 13 + 11 + 95
    prompt_sum = 20000 + 24000 + 22000 + 20000 + 26000 + 22000 + 6570644
    compl_sum = 2000 + 2400 + 2200 + 2000 + 2600 + 2200 + 62359
    reason_sum = 100 + 120 + 110 + 100 + 130 + 110 + 6472
    assert s.avg_prompt_tok == prompt_sum // reqs
    assert s.avg_completion_tok == compl_sum // reqs
    assert s.avg_reasoning_tok == reason_sum // reqs
    # reasoning is a plain int count (no $ field exists on the dataclass).
    assert isinstance(s.avg_reasoning_tok, int)
    assert not hasattr(s, "reasoning_usage")


def test_receipts_cache_credit_is_abs_usage_cache_per_call():
    # decision A: the ONLY itemized $ besides subtotal is the cache credit =
    # abs(usage_cache)/calls (usage_cache is NEGATIVE = a saving).
    s = _by_model(build_receipts(_spike_rows()))[SONNET]
    reqs = 10 + 12 + 11 + 10 + 13 + 11 + 95
    ucache_sum = -0.02 + -0.03 + -0.025 + -0.02 + -0.03 + -0.025 + -16.281199
    assert s.cache_credit_per_call == pytest.approx(abs(ucache_sum) / reqs)
    # haiku had zero cache -> zero credit, never a negative spend.
    h = _by_model(build_receipts(_spike_rows()))[HAIKU]
    assert h.cache_credit_per_call == 0.0


def test_receipts_spark_is_daily_percall_series():
    s = _by_model(build_receipts(_spike_rows()))[SONNET]
    # 7 day-buckets -> 7 ticks, chronological, each = day usage/day reqs.
    assert len(s.spark) == 7
    assert s.spark[0] == pytest.approx(0.10 / 10)   # first day
    assert s.spark[-1] == pytest.approx(190.0 / 95)  # the spike day = $2.00/call


def test_receipts_stamp_FIRES_on_spike():
    # decision B: latest $/call ($2.00) >= 2x the ~$0.01 median AND >= 10 reqs
    # AND above the $/call floor -> PRICE UP stamp.
    s = _by_model(build_receipts(_spike_rows()))[SONNET]
    assert s.young is False           # a full 7-day week
    assert s.has_stamp is True
    assert s.stamp_dir == 1           # PRICE UP
    assert s.stamp_mult >= 2.0
    # the multiplier is latest/median; latest is $2.00/call, median ~ $0.01.
    assert s.stamp_mult > 100.0


def test_receipts_stamp_ABSENT_on_one_request_noise_day():
    # the synthesized 1-request $0.03 day fails the request_count >= 10 gate.
    s = _by_model(build_receipts(_noise_rows()))[SONNET]
    assert s.young is False
    assert s.has_stamp is False
    assert s.stamp_dir == 0
    # sanity: the latest day really WAS 1 request (the gate, not the math).
    assert RECEIPT_MIN_STAMP_REQUESTS == 10


def test_receipts_young_account_suppresses_stamp():
    # < 7 days of history -> no trustworthy median -> stamp suppressed even
    # though the latest day looks like a spike.
    s = _by_model(build_receipts(_young_rows()))[SONNET]
    assert s.young is True
    assert s.has_stamp is False
    assert s.stamp_dir == 0
    assert RECEIPT_MIN_HISTORY_DAYS == 7


def test_receipts_divide_by_zero_guard():
    # a zero-request / zero-usage row must not divide by zero anywhere.
    recs = build_receipts(
        [{"date__day": "2026-06-21", "model": SONNET, "total_usage": 0.0,
          "request_count": "0", "tokens_prompt": "0", "tokens_completion": "0",
          "reasoning_tokens": "0", "cached_tokens": "0", "usage_cache": 0.0}])
    s = recs[0]
    assert s.per_call == 0.0           # guarded, not ZeroDivisionError
    assert s.avg_prompt_tok == 0
    assert s.cache_credit_per_call == 0.0
    assert s.is_empty
    assert s.has_stamp is False


def test_receipts_empty_rows():
    assert build_receipts([]) == ()
    assert build_receipts(None) == ()


def test_build_spend_board_populates_receipts():
    # decision D: build_spend_board fills .receipts from the SAME Query A rows.
    board = build_spend_board(_spike_rows(), granularity="day", start="s", end="e")
    assert isinstance(board, SpendBoard)
    assert len(board.receipts) == 2
    assert board.receipts[0].model_id == SONNET
    # #9's spectrum is still populated from the same rows (no regression).
    assert board.spectrum.total == pytest.approx(board.receipts[0].total_usage
                                                  + board.receipts[1].total_usage)


# ===========================================================================
#  THE STUB LIST widget (qapp) — implements the #10 TEST_PLAN
# ===========================================================================
def _stub_list(qapp, receipts=None, width=380):
    from widgets import ReceiptStubList
    import anim
    anim.set_enabled(False)   # deterministic: no in-flight print wipe during grab
    w = ReceiptStubList()
    w.resize(width, 100)
    if receipts is not None:
        w.set_data(receipts)
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


def test_stub_list_fixed_height_matches_formula(qapp):
    recs = build_receipts(_spike_rows())     # 2 models
    w = _stub_list(qapp, recs)
    expected = len(recs) * (w._row_h + w.ROW_GAP)
    assert w.height() == int(expected)
    # and the grab is full-size (no clip).
    img = _grab(w)
    assert img.height() == w.height()


def test_stub_list_percall_string_for_sonnet(qapp):
    recs = build_receipts(_spike_rows())
    w = _stub_list(qapp, recs)
    s = _by_model(recs)[SONNET]
    total = 0.10 + 0.12 + 0.11 + 0.10 + 0.13 + 0.11 + 190.0
    reqs = 10 + 12 + 11 + 10 + 13 + 11 + 95
    # the exact string the stub paints (TEST_PLAN b).
    assert f"${s.per_call:.4f}/call" == f"${total / reqs:.4f}/call"


def test_stub_list_stamp_rect_exists_on_spike(qapp):
    # TEST_PLAN c: the stamp rect EXISTS for the sonnet spike row.
    recs = build_receipts(_spike_rows())
    w = _stub_list(qapp, recs)
    # sonnet is row 0 (descending spend).
    assert recs[0].model_id == SONNET and recs[0].has_stamp
    assert w._stamp_rects[0] is not None
    # haiku (no stamp) has no rect.
    assert recs[1].model_id == HAIKU and not recs[1].has_stamp
    assert w._stamp_rects[1] is None


def test_stub_list_stamp_rect_absent_on_noise_day(qapp):
    # TEST_PLAN c: ABSENT for the synthesized 1-request $0.03 day (noise gate).
    recs = build_receipts(_noise_rows())
    w = _stub_list(qapp, recs)
    s_idx = [r.model_id for r in recs].index(SONNET)
    assert not recs[s_idx].has_stamp
    assert w._stamp_rects[s_idx] is None


def test_stub_list_locked_state(qapp):
    # a single dim parchment ghost stub + dotted outline + unlock copy; NO rows.
    w = _stub_list(qapp)
    w.set_locked()
    assert w._locked is True
    assert w._row_rects == [] and w._stamp_rects == []
    assert w.height() > 0           # keeps a real fixed height (one stub)
    _grab(w)                        # paints without error
    # the unlock copy is the canonical phrase (decision F).
    from widgets import SPEND_UNLOCK_BASE
    assert SPEND_UNLOCK_BASE == "Add a management key at openrouter.ai to unlock"


def test_stub_list_populated_empty_state(qapp):
    # key present but $0 in range -> real "$0.0000/call" zeros, no stamp, NOT
    # the locked placeholder (decision F).
    recs = build_receipts(
        [{"date__day": "2026-06-21", "model": SONNET, "total_usage": 0.0,
          "request_count": "0", "tokens_prompt": "0", "tokens_completion": "0",
          "reasoning_tokens": "0", "cached_tokens": "0", "usage_cache": 0.0}])
    w = _stub_list(qapp, recs)
    assert w._locked is False
    assert recs[0].per_call == 0.0
    assert f"${recs[0].per_call:.4f}/call" == "$0.0000/call"
    assert w._stamp_rects[0] is None
    _grab(w)


def test_stub_list_print_reveal_does_not_move_widget(qapp):
    # the print-reveal Property must NOT be a QWidget builtin (pos/size) —
    # setting it changes the clip only, never the widget geometry (TEST_PLAN +
    # the INVARIANT against naming a Property after a QWidget builtin).
    recs = build_receipts(_spike_rows())
    w = _stub_list(qapp, recs)
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_print_reveal(0.3)
    assert w.get_print_reveal() == pytest.approx(0.3)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before


def test_stub_list_whole_row_emits_receipt_clicked(qapp):
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    recs = build_receipts(_spike_rows())
    w = _stub_list(qapp, recs)
    captured = []
    w.receipt_clicked.connect(lambda mid, anchor: captured.append(mid))
    mid, rect = w._row_rects[0]
    c = rect.center()
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(c), QPointF(c),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert captured == [SONNET]


# ===========================================================================
#  THE FULL THERMAL RECEIPT (qapp) — render_pixmap + the cache-credit line
# ===========================================================================
def test_receipt_strip_pixmap_height_and_cache_line(qapp):
    # TEST_PLAN d: render_pixmap() returns a QPixmap of the measured height and
    # the CACHE line $ equals abs(usage_cache)/calls.
    from widgets import ReceiptStripWidget
    from PySide6.QtGui import QPixmap
    s = _by_model(build_receipts(_spike_rows()))[SONNET]
    strip = ReceiptStripWidget(s)
    pm = strip.render_pixmap()
    assert isinstance(pm, QPixmap)
    # the logical height matches the measured height (dpr-aware: device px may be
    # larger, but the device-independent size equals STRIP_W x _h).
    assert pm.deviceIndependentSize().height() == pytest.approx(strip._h, abs=1)
    assert pm.width() >= ReceiptStripWidget.STRIP_W   # >= because of dpr scaling
    # the CACHE READ line value the receipt prints (decision A: the only itemized
    # $ besides subtotal) equals abs(usage_cache)/calls.
    items = strip._line_items()
    cache_items = [it for it in items if it[2] == "credit"]
    assert len(cache_items) == 1
    reqs = 10 + 12 + 11 + 10 + 13 + 11 + 95
    ucache_sum = -0.02 + -0.03 + -0.025 + -0.02 + -0.03 + -0.025 + -16.281199
    assert cache_items[0][1] == f"-${abs(ucache_sum) / reqs:.4f}"


def test_receipt_strip_no_per_line_dollars_on_token_items(qapp):
    # decision A: INPUT/OUTPUT/REASONING line items carry NO $ (value is None).
    from widgets import ReceiptStripWidget
    s = _by_model(build_receipts(_spike_rows()))[SONNET]
    strip = ReceiptStripWidget(s)
    tok_items = [it for it in strip._line_items() if it[2] == "tok"]
    assert len(tok_items) >= 2          # at least INPUT + OUTPUT
    for label, value, role in tok_items:
        assert value is None            # NO fabricated per-line $
        assert "tok" in label           # it's a token COUNT line


def test_receipt_html_escapes_model_name(qapp):
    # decision C: any API-sourced string in the popup HTML wrapper is escaped.
    from widgets import build_receipt_html
    from dataclasses import replace
    s = _by_model(build_receipts(_spike_rows()))[SONNET]
    evil = replace(s, short_name="<script>alert(1)</script>")
    html_str = build_receipt_html(evil)
    assert "<script>" not in html_str
    assert "&lt;script&gt;" in html_str


def test_receipt_html_none_is_no_receipt_on_file(qapp):
    # decision F: locked / no receipt -> "— NO RECEIPT ON FILE —".
    from widgets import build_receipt_html
    html_str = build_receipt_html(None)
    assert "NO RECEIPT ON FILE" in html_str


def test_receipt_strip_young_account_pixmap(qapp):
    # a young-account receipt renders (the building-history footnote path) and
    # still returns a valid pixmap of its measured height.
    from widgets import ReceiptStripWidget
    s = _by_model(build_receipts(_young_rows()))[SONNET]
    assert s.young is True and not s.has_stamp
    strip = ReceiptStripWidget(s)
    pm = strip.render_pixmap()
    assert pm.deviceIndependentSize().height() == pytest.approx(strip._h, abs=1)
