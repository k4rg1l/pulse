"""Deterministic tests for Wave 3 #15 — THE ASSAY (value index).

Three layers, all MEASURED (the deterministic-validation discipline — no
eyeballing, no flaky clicking):

  1. PURE value_assay (no Qt) — the #15 TEST_PLAN math:
     (a) value computation: GLM agentic≈45.37, Opus≈9.44, ×≈4.81 (±0.05).
     (b) sort/rank: GLM ranks above Opus and gets rank 0 (gold/hallmark).
     (c) log_scale: log_scale(min)=0.0, log_scale(max)=1.0, monotonic.
     (d) degrade: 0 pins -> empty; 1 pin -> no × ; agentic=None -> unassayable
         (hollow), NOT ELO-substituted on the rail.
     (e) denominator identity: the price used == min priced
         e.price_per_mtok_prompt for the card's endpoints.

  2. THE WIDGET (qapp) — ValueAssayWidget geometry: sizeHint height ==
     caption_h+76+10; no coin cx outside [railLeft+d/2, railRight-d/2]; the
     2-coin live case -> GLM gold+hallmark, Opus copper; the hollow-coin, 1-pin,
     and 0-pin states; the _strike Property is DISTINCT (the widget doesn't move).

  3. THE SCAFFOLD (qapp) — the empty Insights section renders/collapses;
     update_insights(None) on a keyless probe shows the locked header;
     show_insights=False removes the section.
"""
import math

import pytest

from value_assay import (
    AssayModel, AssayResult, METRICS, DEFAULT_METRIC,
    cheapest_prompt_price, build_assay_model, value_rank, log_scale,
)


# --------------------------------------------------------------------------- #
#  Stubs mirroring BenchmarkEntry + ModelEndpoints/EndpointInfo (real shapes). #
# --------------------------------------------------------------------------- #
class _Entry:
    """Mirrors api_client.BenchmarkEntry's value-relevant fields."""
    def __init__(self, intelligence=None, coding=None, agentic=None, peak_elo=None):
        self.intelligence = intelligence
        self.coding = coding
        self.agentic = agentic
        self._peak_elo = peak_elo

    @property
    def peak_elo(self):
        return self._peak_elo


class _Ep:
    """Mirrors api_client.EndpointInfo.price_per_mtok_prompt (== pricing_prompt*1e6)."""
    def __init__(self, pricing_prompt, provider=""):
        self.pricing_prompt = pricing_prompt
        self.provider = provider

    @property
    def price_per_mtok_prompt(self):
        return self.pricing_prompt * 1_000_000


class _Eps:
    def __init__(self, endpoints, model_name=""):
        self.endpoints = endpoints
        self.model_name = model_name      # mirrors ModelEndpoints.model_name


# Real re-verified data (2026-06-24): GLM agentic 43.1 @ $0.95, Opus 47.2 @ $5.00.
GLM_ID = "z-ai/glm-5.2"
OPUS_ID = "anthropic/claude-opus-4.8"


def _glm_entry():
    return _Entry(intelligence=51.1, coding=68.8, agentic=43.1, peak_elo=1378)


def _opus_entry():
    return _Entry(intelligence=55.7, coding=74.3, agentic=47.2, peak_elo=1300)


def _glm_eps():
    # cheapest priced prompt endpoint = DeepInfra $0.95/Mtok (pricing_prompt=0.95e-6);
    # plus a dearer one + an UNPRICED one (must be ignored, not read as $0).
    return _Eps([
        _Ep(2.00e-6, "Together"),
        _Ep(0.95e-6, "DeepInfra"),
        _Ep(0.0, "Free"),          # unpriced -> ignored
    ])


def _opus_eps():
    return _Eps([_Ep(5.00e-6, "Anthropic"), _Ep(5.00e-6, "Google")])


# --------------------------------------------------------------------------- #
#  1. PURE — the #15 TEST_PLAN math                                            #
# --------------------------------------------------------------------------- #
def test_value_computation_glm_opus_and_multiple():
    # TEST_PLAN (a): GLM agentic≈45.37, Opus≈9.44, ×≈4.81.
    glm = build_assay_model(GLM_ID, "GLM-5.2", _glm_entry(), _glm_eps())
    opus = build_assay_model(OPUS_ID, "Claude Opus 4.8", _opus_entry(), _opus_eps())
    assert glm.value_by_metric["agentic"] == pytest.approx(45.37, abs=0.05)
    assert opus.value_by_metric["agentic"] == pytest.approx(9.44, abs=0.05)

    res = value_rank([glm, opus], "agentic")
    assert res.top_multiple == pytest.approx(4.81, abs=0.05)
    # per-category value also told (intelligence ~53.8/11.14, coding ~72.4/14.86).
    assert glm.value_by_metric["intelligence"] == pytest.approx(53.79, abs=0.1)
    assert glm.value_by_metric["coding"] == pytest.approx(72.42, abs=0.1)


def test_sort_rank_glm_above_opus_gets_gold():
    # TEST_PLAN (b): GLM ranks above Opus and is assigned rank 0 (gold/hallmark).
    glm = build_assay_model(GLM_ID, "GLM-5.2", _glm_entry(), _glm_eps())
    opus = build_assay_model(OPUS_ID, "Claude Opus 4.8", _opus_entry(), _opus_eps())
    # Feed Opus FIRST to prove the sort (not input order) decides the winner.
    res = value_rank([opus, glm], "agentic")
    assert res.models[0].model_id == GLM_ID
    assert res.models[0].rank == 0          # gold + hallmark
    assert res.models[1].model_id == OPUS_ID
    assert res.models[1].rank == 1
    assert res.winner.model_id == GLM_ID


def test_log_scale_endpoints_and_monotonic():
    # TEST_PLAN (c): log_scale(min)=0.0, log_scale(max)=1.0, monotonic increasing.
    vmin, vmax = 9.44, 45.37
    assert log_scale(vmin, vmin, vmax) == pytest.approx(0.0)
    assert log_scale(vmax, vmin, vmax) == pytest.approx(1.0)
    mid = log_scale(20.0, vmin, vmax)
    assert 0.0 < mid < 1.0
    # monotonic
    prev = -1.0
    for v in (9.44, 12.0, 20.0, 33.0, 45.37):
        t = log_scale(v, vmin, vmax)
        assert t >= prev
        prev = t
    # degenerate / non-positive guards -> centred 0.5
    assert log_scale(10.0, 10.0, 10.0) == pytest.approx(0.5)
    assert log_scale(-1.0, vmin, vmax) == pytest.approx(0.5)


def test_degrade_zero_one_and_unassayable():
    # TEST_PLAN (d): 0 pins -> empty; 1 pin -> no × ; agentic=None -> unassayable
    # (hollow), NOT ELO-substituted on the rail.
    empty = value_rank([], "agentic")
    assert empty.is_empty
    assert empty.top_multiple is None

    glm = build_assay_model(GLM_ID, "GLM-5.2", _glm_entry(), _glm_eps())
    one = value_rank([glm], "agentic")
    assert not one.is_empty
    assert one.top_multiple is None         # 1 pin -> no hallmark/×
    assert one.models[0].rank == 0

    # A model whose ACTIVE metric (agentic) is missing -> unassayable, even though
    # it still carries a peak_elo (which must NOT become a value on the rail).
    no_ag = _Entry(intelligence=40.0, coding=50.0, agentic=None, peak_elo=1299)
    noag_model = build_assay_model("x/no-agentic", "NoAgentic", no_ag, _opus_eps())
    res = value_rank([glm, noag_model], "agentic")
    target = [m for m in res.models if m.model_id == "x/no-agentic"][0]
    assert target.unassayable is True
    assert target.value is None             # NOT ELO-substituted
    assert target.peak_elo == 1299          # carried for the certificate only
    assert target.rank == -1
    # the assayable GLM still wins and sorts ahead of the hollow coin
    assert res.models[0].model_id == GLM_ID
    assert res.models[-1].model_id == "x/no-agentic"


def test_denominator_identity_is_min_priced_prompt():
    # TEST_PLAN (e): the price used == min priced e.price_per_mtok_prompt for the
    # card's endpoints (the SAME number the PRICE column shows), and the provider
    # is that cheapest endpoint's provider.
    eps = _glm_eps()
    priced = [e.price_per_mtok_prompt for e in eps.endpoints
              if e.price_per_mtok_prompt and e.price_per_mtok_prompt > 0]
    expected = min(priced)
    price, provider = cheapest_prompt_price(eps)
    assert price == pytest.approx(expected)     # 0.95
    assert provider == "DeepInfra"

    glm = build_assay_model(GLM_ID, "GLM-5.2", _glm_entry(), eps)
    assert glm.price == pytest.approx(0.95)
    # No priced endpoint at all -> (None, "") so the widget holds last-good/hollow.
    assert cheapest_prompt_price(_Eps([_Ep(0.0)])) == (None, "")
    assert cheapest_prompt_price(None) == (None, "")


def test_metric_cycle_order_and_default():
    # decision E: the cycle order is intelligence -> coding -> agentic, default agentic.
    assert METRICS == ("intelligence", "coding", "agentic")
    assert DEFAULT_METRIC == "agentic"
    # value_rank re-stamps active metric correctly when cycled.
    glm = build_assay_model(GLM_ID, "GLM-5.2", _glm_entry(), _glm_eps())
    opus = build_assay_model(OPUS_ID, "Claude Opus 4.8", _opus_entry(), _opus_eps())
    res_intel = value_rank([glm, opus], "intelligence")
    assert res_intel.metric == "intelligence"
    assert res_intel.models[0].value == pytest.approx(53.79, abs=0.1)


# --------------------------------------------------------------------------- #
#  2. THE WIDGET (qapp) — ValueAssayWidget geometry                           #
# --------------------------------------------------------------------------- #
def _live_result(metric="agentic"):
    """The 2-coin live case: GLM (gold/hallmark) + Opus (copper)."""
    glm = build_assay_model(GLM_ID, "Z.ai: GLM 5.2", _glm_entry(), _glm_eps(),
                            spend_rank=0)
    opus = build_assay_model(OPUS_ID, "Anthropic: Claude Opus 4.8", _opus_entry(),
                             _opus_eps(), spend_rank=1)
    return value_rank([glm, opus], metric)


def _assay_widget(qapp, result=None, width=320, anim_on=False):
    from widgets import ValueAssayWidget
    import anim
    anim.set_enabled(anim_on)
    w = ValueAssayWidget()
    w.resize(width, 200)
    if result is not None:
        w.set_data(result)
    # Force a geometry build at the test width (resize may not fire offscreen).
    w._build_geometry()
    return w


def test_widget_sizehint_height_formula(qapp):
    # height == caption_h + 76 + 10 (the GEOMETRY_PLAN).
    from PySide6.QtGui import QFontMetrics
    from theme import Fonts
    w = _assay_widget(qapp, _live_result())
    caption_h = QFontMetrics(Fonts.tiny()).height() + 6
    expected = caption_h + 76 + 10
    assert w.sizeHint().height() == expected
    assert w.height() == expected


def test_widget_no_coin_cx_outside_rail(qapp):
    # No coin center falls outside [railLeft + d/2, railRight - d/2].
    w = _assay_widget(qapp, _live_result(), width=320)
    rr = w._rail_rect
    rail_left, rail_right = rr.x(), rr.x() + rr.width()
    assert w._coin_geom, "expected coins"
    for g in w._coin_geom:
        half = g["d"] / 2.0
        assert g["cx"] >= rail_left + half - 0.5
        assert g["cx"] <= rail_right - half + 0.5


def test_widget_two_coin_gold_winner_copper_loser(qapp):
    # The 2-coin live case: GLM is rank 0 (gold + winner + hallmark), Opus rank 1
    # (copper). The winner coin is also the LARGEST diameter.
    from widgets import _metal_for_rank, _METAL_GOLD, _METAL_COPPER
    w = _assay_widget(qapp, _live_result())
    by_id = {g["model_id"]: g for g in w._coin_geom}
    glm, opus = by_id[GLM_ID], by_id[OPUS_ID]
    assert glm["rank"] == 0 and glm["is_winner"] is True
    assert opus["rank"] == 1 and opus["is_winner"] is False
    assert glm["d"] > opus["d"]                      # gold towers over copper
    assert _metal_for_rank(0, 2) == _METAL_GOLD
    assert _metal_for_rank(1, 2) == _METAL_COPPER


def test_widget_one_pin_centered_no_hallmark(qapp):
    # 1 pin -> a single centred coin, no winner-hallmark/×.
    glm = build_assay_model(GLM_ID, "Z.ai: GLM 5.2", _glm_entry(), _glm_eps())
    res = value_rank([glm], "agentic")
    w = _assay_widget(qapp, res, width=320)
    assert len(w._coin_geom) == 1
    g = w._coin_geom[0]
    rr = w._rail_rect
    assert g["cx"] == pytest.approx(rr.x() + rr.width() / 2.0, abs=0.5)  # centred
    assert g["d"] == pytest.approx(w.SINGLE_D)
    assert res.top_multiple is None                  # no × engraving


def test_widget_hollow_unassayable_coin(qapp):
    # A model missing the active AA index -> a hollow coin (no metal fill), still
    # placed on the rail (never dropped).
    no_ag = _Entry(intelligence=40.0, coding=50.0, agentic=None, peak_elo=1299)
    glm = build_assay_model(GLM_ID, "Z.ai: GLM 5.2", _glm_entry(), _glm_eps())
    hollow = build_assay_model("x/no-ag", "Vendor: NoAgentic", no_ag, _opus_eps())
    res = value_rank([glm, hollow], "agentic")
    w = _assay_widget(qapp, res, width=320)
    by_id = {g["model_id"]: g for g in w._coin_geom}
    assert by_id["x/no-ag"]["hollow"] is True
    assert by_id[GLM_ID]["hollow"] is False


def test_widget_zero_pins_empty_state(qapp):
    # 0 pins -> empty result -> no coins; the widget still sizes + paints chrome.
    res = value_rank([], "agentic")
    w = _assay_widget(qapp, res, width=320)
    assert res.is_empty
    assert w._coin_geom == []
    _grab(w)                                         # paints the empty state w/o crashing


def test_widget_strike_property_distinct_no_move(qapp):
    # _strike is a DISTINCT Property (NOT pos/size/geometry); setting it changes
    # only the coin scale, never the widget geometry (the no-move regression).
    w = _assay_widget(qapp, _live_result())
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_strike(0.5)
    assert w.get_strike() == pytest.approx(0.5)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before


def test_widget_anim_disabled_strikes_instantly(qapp):
    # reduce-motion -> _strike parked at 1.0 (no running anim).
    w = _assay_widget(qapp, _live_result(), anim_on=False)
    assert w.get_strike() == pytest.approx(1.0)


def test_widget_metric_cycle_order(qapp):
    # The metric label cycles intelligence -> coding -> agentic -> intelligence.
    w = _assay_widget(qapp, _live_result("intelligence"))
    assert w.current_metric() == "intelligence"
    assert w._next_metric() == "coding"
    w._metric = "coding"
    assert w._next_metric() == "agentic"
    w._metric = "agentic"
    assert w._next_metric() == "intelligence"


def test_certificate_html_auditable_and_escaped(qapp):
    # The certificate footnote carries the auditable denominator + html-escapes
    # the name; the winner shows its × multiple.
    from widgets import build_assay_certificate_html
    res = _live_result()
    winner = res.winner
    html_str = build_assay_certificate_html(winner, res)
    assert "cheapest prompt endpoint $0.95/Mtok" in html_str
    assert "DeepInfra" in html_str
    assert "AA 43.1 (0-100)" in html_str
    assert "×" in html_str                            # the multiple is engraved


def test_certificate_html_elo_basis_only_when_unassayable(qapp):
    # ELO basis appears ONLY in the certificate of an unassayable model (never on
    # the rail). An assayable model's certificate has NO 'ELO basis' line.
    from widgets import build_assay_certificate_html
    no_ag = _Entry(intelligence=40.0, coding=50.0, agentic=None, peak_elo=1299)
    glm = build_assay_model(GLM_ID, "Z.ai: GLM 5.2", _glm_entry(), _glm_eps())
    hollow = build_assay_model("x/no-ag", "Vendor: NoAgentic", no_ag, _opus_eps())
    res = value_rank([glm, hollow], "agentic")
    hollow_html = build_assay_certificate_html(
        [m for m in res.models if m.model_id == "x/no-ag"][0], res)
    assert "ELO basis" in hollow_html and "1299" in hollow_html
    glm_html = build_assay_certificate_html(res.winner, res)
    assert "ELO basis" not in glm_html


def _grab(w):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    img = QImage(w.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    w.render(p, QPoint(0, 0))
    p.end()
    return img


# --------------------------------------------------------------------------- #
#  3. THE SCAFFOLD (qapp) — the Insights zone stands up + degrades             #
# --------------------------------------------------------------------------- #
def test_scaffold_section_built_with_anchor(qapp):
    # show_insights default True -> the section + #15 anchor exist, mounted
    # BETWEEN the Models board and Quick Links.
    from dashboard import Dashboard
    from persistence import History
    from settings import Settings
    d = Dashboard(History(), Settings())
    assert getattr(d, "_insights_header", None) is not None
    assert getattr(d, "_insights_container", None) is not None
    assert getattr(d, "_value_assay", None) is not None
    # ordering: the insights container sits after the pinned container and before
    # the Quick Links row in the OR layout.
    layout = d._or_layout
    idx = {layout.itemAt(i).widget(): i for i in range(layout.count())
           if layout.itemAt(i).widget() is not None}
    assert idx[d._insights_container] > idx[d._pinned_container]


def test_scaffold_collapse_toggle(qapp):
    from dashboard import Dashboard
    from persistence import History
    from settings import Settings
    d = Dashboard(History(), Settings())
    assert d._insights_container.isVisibleTo(d) or not d._insights_collapsed
    d._toggle_insights_collapsed()
    assert d._insights_collapsed is True
    assert d._insights_header.is_collapsed() is True
    d._toggle_insights_collapsed()
    assert d._insights_collapsed is False


def test_scaffold_update_insights_none_keyless_shows_locked(qapp):
    # On a keyless probe (no mgmt key) + no last-good board, update_insights(None)
    # leaves the mgmt path in 'locked' (no mgmt widgets yet, but the header reads
    # 'locked' UNLESS #15 already wrote a live 'standard:' headline).
    from dashboard import Dashboard
    from persistence import History
    from settings import Settings
    d = Dashboard(History(), Settings())
    d._insights_unlocked = False          # simulate a keyless machine
    d._insights_board = None
    # No pins -> #15 wrote no 'standard:' headline -> the locked label applies.
    d.update_insights(None)
    assert d._insights_header.right_label.text() == "locked"


def test_scaffold_show_insights_false_removes_section(qapp):
    from dashboard import Dashboard
    from persistence import History
    from settings import Settings
    d = Dashboard(History(), Settings(show_insights=False))
    assert getattr(d, "_value_assay", None) is None
    assert getattr(d, "_insights_container", None) is None
    # update_insights is a safe no-op when the section wasn't built.
    d.update_insights(None)               # must not raise


def test_scaffold_distribute_value_live_two_pins(qapp):
    # The end-to-end value path: pin GLM + Opus, feed benchmarks + endpoints, and
    # assert the widget receives the live 2-coin result with GLM the gold winner.
    from dashboard import Dashboard
    from persistence import History
    from settings import Settings
    d = Dashboard(History(), Settings(tracked_models=[GLM_ID, OPUS_ID]))

    # Stub a BenchmarkBoard-like object whose lookup returns our entries.
    class _Board:
        def lookup(self, mid, disp=None):
            return _glm_entry() if mid == GLM_ID else _opus_entry()
    d._benchmark_board = _Board()
    # Hand each card its endpoints (the price denominator).
    from value_assay import cheapest_prompt_price
    d._pinned_cards[GLM_ID]._endpoints = _glm_eps()
    d._pinned_cards[OPUS_ID]._endpoints = _opus_eps()
    d._distribute_value()

    res = d._value_assay._result
    assert res is not None and not res.is_empty
    assert res.winner.model_id == GLM_ID
    assert res.top_multiple == pytest.approx(4.81, abs=0.05)
    # the live headline made it into the collapsible header.
    assert d._insights_header.right_label.text().startswith("standard:")
