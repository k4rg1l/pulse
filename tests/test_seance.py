"""Deterministic tests for Wave 2 #13 — THE SÉANCE (ghost model detector).

Two layers, both MEASURED (the deterministic-validation discipline — no
eyeballing, no flaky clicking):

  1. PURE — parse_ghost_diff(envA, envB) against three fixtures carrying the
     LIVE-shaped quirks (created_at__week bucket key, request_count a STRING):
       (i)  a SYNTHETIC POPULATED fixture (A and B carry DISTINCT week buckets):
            1 living, 1 vanished, 1 appeared -> correct A∩B / B−A / A−B sets,
            created_at__week detection, _as_int over the string request_count.
       (ii) a YOUNG fixture (only ONE distinct week bucket / B empty) ->
            young_history True, appeared/vanished empty, living = A's pairs —
            NO phantom apparitions (the live state, decision A).
       (iii) a RE-ROUTE fixture (same model, NEW provider across the two weeks)
            -> the appeared+vanished entries flag reroute=True (decision C).

  2. THE WIDGET (qapp) — GhostVeil.set_data() the fixtures and MEASURE the #13
     TEST_PLAN: (a) setFixedHeight == the ~94px populated formula; (b) the living
     chip center y is ABOVE the veil y and the vanished chip's is BELOW; (c)
     exactly one apparition ring rect + the materialize anim created/started
     once; (d) _glyph_rects has 3 entries + a synthesized mousePress on the
     vanished chip emits ghost_clicked for the right pair. Plus the young
     ("watching") + calm ("veil is still") + locked (padlock + unlock copy)
     states, the count-doesn't-move regression (materialize is NOT a QWidget
     builtin), and the html.escape of model/provider names in the ledger HTML.
"""
import pytest

import api_client as a
from api_client import (
    parse_ghost_diff, build_ghost_diff, GhostDiff, GhostEntry, GhostPair,
    build_spend_board, SpendBoard,
)

SONNET = "anthropic/claude-4.6-sonnet-20260217"
HAIKU = "anthropic/claude-4.5-haiku-20251001"
OPUS = "anthropic/claude-4.6-opus-20260101"

WK_A = "2026-06-21"   # the latest week bucket (Window A)
WK_B = "2026-06-14"   # the prior week bucket (Window B)


def _row(bucket, model, provider, reqs, usage):
    """A verbatim-shaped week row: created_at__week key + STRING request_count."""
    return {
        "created_at__week": bucket,
        "model": model,
        "provider": provider,
        "request_count": str(reqs),    # STRING, as the live API returns it
        "total_usage": usage,          # float
    }


def _env(rows):
    """Wrap rows in a parsed-envelope dict (parse_ghost_diff also accepts a bare
    list, but the production path hands it the {"rows": ...} parsed envelope)."""
    return {"rows": rows, "metadata": {}, "cachedAt": None}


# ===========================================================================
#  Fixtures
# ===========================================================================
def _populated_envs():
    """A and B carry DISTINCT week buckets so the diff fires:
      - LIVING:   (sonnet, Anthropic) in BOTH weeks.
      - VANISHED: (haiku, Amazon Bedrock) only in the PRIOR week (B).
      - APPEARED: (opus, OpenAI) only THIS week (A) — a never-before-seen model.
    """
    rows_a = [
        _row(WK_A, SONNET, "Anthropic", 87, 4.143954),
        _row(WK_A, OPUS, "OpenAI", 40, 9.990000),          # APPEARED (runaway)
    ]
    rows_b = [
        _row(WK_B, SONNET, "Anthropic", 50, 2.500000),
        _row(WK_B, HAIKU, "Amazon Bedrock", 3, 0.002088),  # VANISHED
    ]
    return _env(rows_a), _env(rows_b)


def _young_envs():
    """Only ONE distinct week bucket exists (B empty) -> the LIVE young state."""
    rows_a = [
        _row(WK_A, SONNET, "Anthropic", 87, 4.143954),
        _row(WK_A, SONNET, "Google", 15, 0.310157),
        _row(WK_A, HAIKU, "Amazon Bedrock", 3, 0.002088),
    ]
    return _env(rows_a), _env([])


def _calm_envs():
    """B HAS data (two distinct weeks) but the (model,provider) sets are
    IDENTICAL -> 0 ghosts, NOT young (the 'veil is still' calm state)."""
    rows_a = [
        _row(WK_A, SONNET, "Anthropic", 87, 4.143954),
        _row(WK_A, HAIKU, "Amazon Bedrock", 3, 0.002088),
    ]
    rows_b = [
        _row(WK_B, SONNET, "Anthropic", 50, 2.500000),
        _row(WK_B, HAIKU, "Amazon Bedrock", 2, 0.001500),
    ]
    return _env(rows_a), _env(rows_b)


def _reroute_envs():
    """Same MODEL, NEW provider across the two weeks: sonnet was on Google in B,
    is on Anthropic in A. That's one vanish + one appear, but BOTH must carry
    reroute=True (a benign re-route, decision C — not a runaway)."""
    rows_a = [_row(WK_A, SONNET, "Anthropic", 87, 4.143954)]
    rows_b = [_row(WK_B, SONNET, "Google", 50, 2.500000)]
    return _env(rows_a), _env(rows_b)


def _keys(entries):
    return {e.pair.key for e in entries}


# ===========================================================================
#  PURE — parse_ghost_diff
# ===========================================================================
def test_populated_diff_sets_are_correct():
    diff = parse_ghost_diff(*_populated_envs())
    assert not diff.young_history
    assert _keys(diff.living) == {(SONNET, "Anthropic")}
    assert _keys(diff.vanished) == {(HAIKU, "Amazon Bedrock")}
    assert _keys(diff.appeared) == {(OPUS, "OpenAI")}
    assert diff.has_ghosts is True


def test_populated_aligns_to_returned_week_buckets():
    # decision B: windows align to the API's RETURNED created_at__week dates
    # (latest = A, 2nd-latest = B), NOT a client calendar week.
    diff = parse_ghost_diff(*_populated_envs())
    assert diff.week_bucket_a == WK_A
    assert diff.week_bucket_b == WK_B


def test_request_count_string_is_coerced_via_as_int():
    # the live API returns request_count as a STRING; the carried figures are int.
    assert a._as_int("87") == 87
    diff = parse_ghost_diff(*_populated_envs())
    appeared = diff.appeared[0]
    assert appeared.this.request_count == 40
    assert isinstance(appeared.this.request_count, int)
    assert appeared.this.usage == pytest.approx(9.99)
    vanished = diff.vanished[0]
    assert vanished.prior.request_count == 3      # carries B's prior-week figure
    assert vanished.this is None                  # gone this week


def test_appeared_is_never_seen_before_flag():
    # an APPEARED pair whose MODEL is new -> reroute False (a true apparition).
    diff = parse_ghost_diff(*_populated_envs())
    apparition = diff.appeared[0]
    assert apparition.pair.model_id == OPUS
    assert apparition.reroute is False            # genuinely new, the scary event


def test_living_carries_both_window_figures():
    diff = parse_ghost_diff(*_populated_envs())
    sonnet = diff.living[0]
    assert sonnet.pair.key == (SONNET, "Anthropic")
    assert sonnet.this.request_count == 87        # this-week
    assert sonnet.prior.request_count == 50       # prior-week (for the timeline)


def test_rank_matches_descending_spend_for_shared_color():
    # decision D: rank == the model's descending-spend rank across BOTH windows
    # so the chip color == that model's #9 spectrum band.
    diff = parse_ghost_diff(*_populated_envs())
    # sonnet total = 4.14+2.5 = 6.64 ; opus = 9.99 ; haiku = 0.002 -> opus rank 0.
    by_model = {}
    for e in list(diff.living) + list(diff.vanished) + list(diff.appeared):
        by_model[e.pair.model_id] = e.rank
    assert by_model[OPUS] == 0       # heaviest single-week spender => accent
    assert by_model[SONNET] == 1
    assert by_model[HAIKU] == 2


def test_young_history_suppresses_diff_no_phantoms():
    # decision A — THE critical correctness gate. B empty / one distinct week ->
    # young_history True, NO appeared/vanished (would otherwise be EVERY pair).
    diff = parse_ghost_diff(*_young_envs())
    assert diff.young_history is True
    assert diff.appeared == ()
    assert diff.vanished == ()
    # living = the single week's pairs (the honest roster).
    assert _keys(diff.living) == {
        (SONNET, "Anthropic"), (SONNET, "Google"), (HAIKU, "Amazon Bedrock")
    }
    assert diff.has_ghosts is False


def test_young_history_with_b_literally_empty_rows():
    # belt-and-suspenders: a totally empty B envelope still -> young (1 week).
    env_a, _ = _young_envs()
    diff = parse_ghost_diff(env_a, _env([]))
    assert diff.young_history is True
    assert len(diff.living) == 3


def test_calm_state_zero_ghosts_not_young():
    # B HAS data + identical pair sets -> 0 ghosts but young_history False.
    diff = parse_ghost_diff(*_calm_envs())
    assert diff.young_history is False
    assert diff.appeared == ()
    assert diff.vanished == ()
    assert len(diff.living) == 2
    assert diff.has_ghosts is False


def test_reroute_same_model_new_provider_flagged():
    # decision C: sonnet moved Google->Anthropic -> one vanish + one appear, BOTH
    # reroute=True (a benign re-route, NOT a runaway apparition).
    diff = parse_ghost_diff(*_reroute_envs())
    assert _keys(diff.appeared) == {(SONNET, "Anthropic")}
    assert _keys(diff.vanished) == {(SONNET, "Google")}
    assert diff.appeared[0].reroute is True
    assert diff.vanished[0].reroute is True


def test_parse_accepts_bare_row_lists():
    env_a, env_b = _populated_envs()
    diff = parse_ghost_diff(env_a["rows"], env_b["rows"])
    assert not diff.young_history
    assert _keys(diff.appeared) == {(OPUS, "OpenAI")}


def test_build_ghost_diff_is_parse_alias():
    assert build_ghost_diff is parse_ghost_diff


def test_parse_never_raises_on_garbage():
    # None / malformed rows -> a calm young GhostDiff, never an exception.
    diff = parse_ghost_diff(None, None)
    assert isinstance(diff, GhostDiff)
    assert diff.young_history is True
    assert diff.living == ()
    diff2 = parse_ghost_diff(_env([{"garbage": 1}, "not-a-dict"]), _env([]))
    assert isinstance(diff2, GhostDiff)


def test_build_spend_board_carries_ghosts_through():
    # the board passes a ghosts payload straight through (Query A rows unrelated).
    env_a, env_b = _populated_envs()
    ghosts = parse_ghost_diff(env_a, env_b)
    rows_q_a = [
        {"date__day": "2026-06-21", "model": SONNET, "total_usage": 4.0,
         "request_count": "95", "tokens_prompt": "1000",
         "tokens_completion": "100", "reasoning_tokens": "0",
         "cached_tokens": "0", "usage_cache": 0.0, "cache_hit_rate": 0.0},
    ]
    board = build_spend_board(rows_q_a, granularity="day", start="s", end="e",
                              ghosts=ghosts)
    assert isinstance(board, SpendBoard)
    assert board.ghosts is ghosts
    assert board.ghosts.has_ghosts is True
    # #9/#10/#12 still populate from Query A (no regress).
    assert board.spectrum.total > 0


# ===========================================================================
#  THE GHOST VEIL widget (qapp) — implements the #13 TEST_PLAN
# ===========================================================================
def _veil(qapp, diff=None, width=380, anim_on=False):
    from widgets import GhostVeil
    import anim
    anim.set_enabled(anim_on)
    w = GhostVeil()
    w.resize(width, 120)
    if diff is not None:
        w.set_data(diff)
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


def test_veil_populated_fixed_height_is_94(qapp):
    # TEST_PLAN a: setFixedHeight == the ~94px populated formula.
    w = _veil(qapp, parse_ghost_diff(*_populated_envs()))
    from widgets import GhostVeil
    assert w.height() == GhostVeil._populated_height()
    assert w.height() == 94
    img = _grab(w)
    assert img.height() == 94          # no clip


def test_veil_living_above_vanished_below(qapp):
    # TEST_PLAN b: the living chip center y is ABOVE the veil y; the vanished
    # chip's center y is BELOW it (the spatial alive/gone encoding).
    w = _veil(qapp, parse_ghost_diff(*_populated_envs()))
    _grab(w)                            # populate cached geometry
    veil_y = w._veil_y
    living = [r for (r, key, role) in w._glyph_rects if role == "living"]
    vanished = [r for (r, key, role) in w._glyph_rects if role == "vanished"]
    appeared = [r for (r, key, role) in w._glyph_rects if role == "appeared"]
    assert living and vanished and appeared
    for r in living + appeared:         # appeared sit ABOVE too (they're alive)
        assert r.center().y() < veil_y
    for r in vanished:
        assert r.center().y() > veil_y


def test_veil_exactly_one_apparition_ring_and_anim_once(qapp):
    # TEST_PLAN c: exactly one apparition ring rect + the materialize anim is
    # created and started once (animations ON here). The widget must be visible
    # for the one-shot to run (a hidden widget parks the ring static).
    from widgets import GhostVeil
    import anim
    anim.set_enabled(True)
    w = GhostVeil()
    w.resize(380, 120)
    w.show()                       # isVisible() True -> the live one-shot runs
    w.set_data(parse_ghost_diff(*_populated_envs()))
    _grab(w)
    assert len(w._apparition_rings) == 1
    assert w._materialize_started_count == 1
    assert w._anim is not None
    # a 2nd identical poll must NOT re-animate (the signature gate).
    w.set_data(parse_ghost_diff(*_populated_envs()))
    assert w._materialize_started_count == 1


def test_veil_glyph_rects_and_vanished_click_emits(qapp):
    # TEST_PLAN d: _glyph_rects has 3 entries + a synthesized mousePress on the
    # vanished chip emits ghost_clicked for the RIGHT pair.
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    w = _veil(qapp, parse_ghost_diff(*_populated_envs()))
    _grab(w)
    assert len(w._glyph_rects) == 3
    vrect, vkey, _role = next((r, k, role) for (r, k, role) in w._glyph_rects
                              if role == "vanished")
    captured = []
    w.ghost_clicked.connect(lambda key, anchor: captured.append(key))
    c = vrect.center()
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, c, c,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert captured == [vkey]
    assert vkey == (HAIKU, "Amazon Bedrock")


def test_veil_young_caption_and_no_rings(qapp):
    # young fixture: the 'watching' caption renders, NO apparition rings, height
    # is the young/calm (collapsed) formula.
    from widgets import GhostVeil
    w = _veil(qapp, parse_ghost_diff(*_young_envs()))
    _grab(w)
    assert w._apparition_rings == []
    assert w._caption_text and "watching" in w._caption_text.lower()
    assert w.height() == GhostVeil._calm_height()
    assert w.height() == 62
    # living roster still painted (3 chips above the veil).
    living = [r for (r, key, role) in w._glyph_rects if role == "living"]
    assert len(living) == 3


def test_veil_calm_caption_veil_is_still(qapp):
    # calm fixture (B has data, 0 ghosts): the 'veil is still' caption renders.
    w = _veil(qapp, parse_ghost_diff(*_calm_envs()))
    _grab(w)
    assert w._caption_text and "veil is still" in w._caption_text.lower()
    assert w._apparition_rings == []


def test_veil_locked_state(qapp):
    # decision F: dim DASHED hairline + padlock + the canonical unlock copy.
    from widgets import GhostVeil, SPEND_UNLOCK_BASE
    w = _veil(qapp)
    w.set_locked()
    assert w._locked is True
    assert w._diff is None
    assert w._glyph_rects == []
    assert w._caption_text and "unlock ghost detection" in w._caption_text.lower()
    assert SPEND_UNLOCK_BASE == "Add a management key at openrouter.ai to unlock"
    _grab(w)                            # paints padlock + dashed hairline w/o error


def test_veil_materialize_is_not_a_qwidget_builtin(qapp):
    # INVARIANT: the materialize Property must NOT move the widget (it's not
    # pos/size/geometry). Setting it changes only the ring, never the geometry.
    w = _veil(qapp, parse_ghost_diff(*_populated_envs()))
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_materialize(0.5)
    assert w.get_materialize() == pytest.approx(0.5)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before


def test_veil_anim_disabled_draws_static_ring(qapp):
    # animations OFF: NO running anim, but a static ring still drawn (additive).
    w = _veil(qapp, parse_ghost_diff(*_populated_envs()), anim_on=False)
    _grab(w)
    assert len(w._apparition_rings) == 1     # the ring rect still measured/drawn
    # materialize parked at 1.0 (fully resolved) when anim is off.
    assert w.get_materialize() == pytest.approx(1.0)


def test_veil_set_data_none_keeps_last_good(qapp):
    w = _veil(qapp, parse_ghost_diff(*_populated_envs()))
    sig_before = w._signature
    w.set_data(None)                    # keep last-good, no blank
    assert w._diff is not None
    assert w._signature == sig_before


# ----- the SÉANCE LEDGER popup HTML -----
def test_ledger_html_escapes_names(qapp):
    # decision E: html.escape every model/provider name in the ledger HTML.
    from widgets import build_seance_html
    evil_model = 'anthropic/<script>"&x'
    evil_prov = 'Prov<img>'
    env_a = _env([_row(WK_A, evil_model, evil_prov, 5, 1.0)])
    env_b = _env([_row(WK_B, evil_model, evil_prov, 3, 0.5)])
    diff = parse_ghost_diff(env_a, env_b)
    entry = (list(diff.living) + list(diff.appeared) + list(diff.vanished))[0]
    html_str = build_seance_html(entry, diff)
    assert "<script>" not in html_str
    assert "&lt;script&gt;" in html_str
    assert "<img>" not in html_str
    assert "&lt;img&gt;" in html_str


def test_ledger_html_reroute_note(qapp):
    # decision C: the ledger explains a same-model-new-provider move as a re-route.
    from widgets import build_seance_html
    diff = parse_ghost_diff(*_reroute_envs())
    entry = diff.appeared[0]
    html_str = build_seance_html(entry, diff)
    assert "re-route" in html_str.lower() or "reroute" in html_str.lower()


def test_ledger_html_apparition_never_seen(qapp):
    from widgets import build_seance_html
    diff = parse_ghost_diff(*_populated_envs())
    entry = diff.appeared[0]            # opus/OpenAI, a true apparition
    html_str = build_seance_html(entry, diff)
    assert "never seen before" in html_str.lower()


def test_ghost_accent_is_model_color_not_crimson(qapp):
    # decision D: a chip's accent is the model's shared color, NEVER crimson/red.
    from widgets import ghost_accent_hex
    from theme import Colors
    diff = parse_ghost_diff(*_populated_envs())
    entry = diff.living[0]
    hex_c = ghost_accent_hex(entry)
    assert hex_c != Colors.RED.name()
    # it matches the shared palette for this model's rank.
    import spend_palette
    assert hex_c == spend_palette.model_color(entry.pair.model_id,
                                              entry.rank).name()
