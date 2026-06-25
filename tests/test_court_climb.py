"""Deterministic tests for Wave 3 #18 — THE COURT & THE CLIMB (Task Crown +
the honest base-camp apps ladder). THE FINALE.

Two layers, all MEASURED (the deterministic-validation discipline — no eyeballing,
no flaky clicking, no hallucinated success):

  1. PURE task_court + frontend_client (no Qt) — the #18 TEST_PLAN math:
     (a) crown selection: a classifications stub {agent: deepseek-v4-flash
         share 0.31 > other 0.12} -> agent crown = deepseek-v4-flash; code ->
         mimo-v2.5.
     (b) parse coercion: parse_rankings_apps coerces total_tokens STRING
         '7311066568924' -> int, keeps total_requests int, rank order preserved.
     (c) log placement + valley: summit=7.16e12 / floor=71e9 / user=6.7e6 ->
         summit y≈top, floor y≈bottom, user y BELOW the floor (valley, NOT
         clamped equal).
     (d) HONEST-CLAIM GUARD: scan the built strings + BOTH dossiers' HTML for
         'out-tokened' (case-insensitive) -> assert ABSENT when user < floor.
     (e) window guard: get_task_classifications always sends window=7d.
     (f) degrade: classifications=None -> court 'unavailable' flag + climb still
         builds; mgmt-locked (top_model None) -> ember overlays dropped, summit
         still rendered.

  2. THE WIDGET (qapp) — TaskCourt: the COURT renders 4 crowns + the ember chip
     on ONE seat; the CLIMB renders the rope ladder with the user marker BELOW the
     floor rung; the `ascent` climb-in fires ONCE + skips on same-data re-poll +
     the Property is DISTINCT (no-move); classifications=None -> 'world task board
     unavailable' + the climb still renders; mgmt-locked -> ember dropped.
"""
import math

import pytest

from frontend_client import parse_rankings_apps, AppRanking
from task_court import (
    parse_task_classifications, build_court_climb,
    abbr_tokens, log_y_frac, _thin_rungs, _abbr_multiple,
    TaskBoard, MacroCrown, CourtClimb, CourtSeat, ClimbRung,
    FORBIDDEN_CLAIM, DEFAULT_EMBER_MACRO, CLIMB_RUNG_TARGET,
)


# --------------------------------------------------------------------------- #
#  Fixtures — the LIVE shapes (re-verified 2026-06-24).                        #
#   classifications/task: macro_categories[] + classifications[] with          #
#   models[{id, tag_token_share}]; the crowns code->mimo-v2.5,                  #
#   agent/data/general->deepseek-v4-flash.                                      #
#   rankings/apps: week[] rows with total_tokens STRING, total_requests int.    #
# --------------------------------------------------------------------------- #
def _classifications_stub():
    return {
        "window_days": 7,
        "as_of": "2026-06-23",
        "macro_categories": [
            {"key": "code", "label": "Code", "token_share": 0.20},
            {"key": "data", "label": "Data", "token_share": 0.15},
            {"key": "agent", "label": "Agentic", "token_share": 0.30},
            {"key": "general", "label": "General", "token_share": 0.35},
        ],
        "classifications": [
            {"macro_category": "agent", "tag": "tool-use", "token_share": 0.3,
             "models": [
                 {"id": "deepseek/deepseek-v4-flash-20260423", "tag_token_share": 0.31},
                 {"id": "anthropic/claude-opus-4.8", "tag_token_share": 0.12},
             ]},
            {"macro_category": "code", "tag": "autocomplete", "token_share": 0.2,
             "models": [
                 {"id": "xiaomi/mimo-v2.5-20260422", "tag_token_share": 0.40},
                 {"id": "qwen/qwen-4", "tag_token_share": 0.10},
             ]},
            {"macro_category": "data", "tag": "etl", "token_share": 0.15,
             "models": [
                 {"id": "deepseek/deepseek-v4-flash-20260423", "tag_token_share": 0.27},
             ]},
            {"macro_category": "general", "tag": "chat", "token_share": 0.35,
             "models": [
                 {"id": "deepseek/deepseek-v4-flash-20260423", "tag_token_share": 0.50},
             ]},
        ],
    }


def _apps_week_stub(n=20):
    """A descending top-N apps board, summit ~7.31T down to a ~73B floor — the
    real magnitudes. total_tokens is a STRING (the live shape)."""
    rows = []
    # summit Hermes 7.31T, then a geometric descent down to ~73B at the floor.
    summit = 7_311_066_568_924
    floor = 73_272_973_755
    for i in range(n):
        frac = i / (n - 1) if n > 1 else 0.0
        if i == 0:
            tok = summit                       # exact summit (avoid float drift)
        elif i == n - 1:
            tok = floor                        # exact floor
        else:
            # log-interpolate so the descent is smooth in log space.
            lt = math.log10(summit) + frac * (math.log10(floor) - math.log10(summit))
            tok = int(10 ** lt)
        rows.append({
            "rank": i + 1,
            "total_tokens": str(tok),          # STRING (the coercion target)
            "total_requests": 1000 - i,        # int
            "app": {"title": f"App {i+1}" if i else "Hermes Agent",
                    "slug": f"app-{i+1}", "favicon_url": f"/f{i}.png"},
        })
    return {"day": [], "week": rows, "month": []}


USER_TOKENS = 6_697_136            # the live #17 lifetime total (~6.7M)
USER_APP = "OpenCode"
TOP_MODEL = "anthropic/claude-4.6-sonnet-20260217"   # the live #16 champion


# =========================================================================== #
#  1. PURE — the #18 TEST_PLAN math                                            #
# =========================================================================== #

# (a) CROWN SELECTION ------------------------------------------------------- #
def test_crown_selection_agent_and_code():
    # TEST_PLAN (a): agent crown = deepseek-v4-flash (0.31 > 0.12); code crown =
    # mimo-v2.5 (0.40 > 0.10). The crown is the max-aggregated-share model.
    tb = parse_task_classifications(_classifications_stub())
    agent = tb.crown_for("agent")
    code = tb.crown_for("code")
    assert agent.world_model == "deepseek/deepseek-v4-flash-20260423"
    assert code.world_model == "xiaomi/mimo-v2.5-20260422"
    # data + general are ALSO deepseek-v4-flash (the live world board).
    assert tb.crown_for("data").world_model == "deepseek/deepseek-v4-flash-20260423"
    assert tb.crown_for("general").world_model == "deepseek/deepseek-v4-flash-20260423"


def test_crown_canonical_macro_order():
    # The seats render in the canonical code/agent/data/general order regardless
    # of the API's return order (the stub lists agent first).
    tb = parse_task_classifications(_classifications_stub())
    assert [c.macro for c in tb.crowns] == ["code", "agent", "data", "general"]


def test_crown_aggregates_share_across_tasks():
    # A model spread over TWO tasks in a macro aggregates; the bigger SUM wins.
    data = {
        "macro_categories": [{"key": "code", "label": "Code", "token_share": 0.5}],
        "classifications": [
            {"macro_category": "code", "models": [
                {"id": "split-model", "tag_token_share": 0.20}]},
            {"macro_category": "code", "models": [
                {"id": "split-model", "tag_token_share": 0.25},   # sum 0.45
                {"id": "single", "tag_token_share": 0.40}]},      # < 0.45
        ],
    }
    tb = parse_task_classifications(data)
    assert tb.crown_for("code").world_model == "split-model"
    assert tb.crown_for("code").model_share == pytest.approx(0.45)


def test_crown_world_share_is_macro_token_share():
    # The gold underline width = the macro's token_share (the world's appetite for
    # that task), NOT the crowned model's within-macro share.
    tb = parse_task_classifications(_classifications_stub())
    assert tb.crown_for("agent").world_share == pytest.approx(0.30)
    assert tb.crown_for("general").world_share == pytest.approx(0.35)


# (b) PARSE COERCION -------------------------------------------------------- #
def test_parse_rankings_apps_coerces_string_tokens():
    # TEST_PLAN (b): total_tokens STRING '7311066568924' -> int; total_requests
    # stays int; rank order preserved.
    apps = parse_rankings_apps(_apps_week_stub())
    assert len(apps) == 20
    assert apps[0].total_tokens == 7311066568924
    assert isinstance(apps[0].total_tokens, int)
    assert isinstance(apps[0].total_requests, int)
    assert apps[0].total_requests == 1000
    # rank order preserved (summit-first as the API returns).
    assert [a.rank for a in apps] == list(range(1, 21))
    assert apps[0].title == "Hermes Agent" and apps[0].slug == "app-1"


def test_parse_rankings_apps_reads_week_not_day():
    # The parser reads the WEEK list (weekly, comparable to the user's weekly
    # total) — NOT day/month.
    data = {"day": [{"rank": 9, "total_tokens": "1", "total_requests": 1,
                     "app": {"title": "DAY", "slug": "d"}}],
            "week": [{"rank": 1, "total_tokens": "999", "total_requests": 2,
                      "app": {"title": "WEEK", "slug": "w"}}],
            "month": []}
    apps = parse_rankings_apps(data)
    assert len(apps) == 1 and apps[0].title == "WEEK"


def test_parse_rankings_apps_degrades_on_junk():
    # Missing/odd payloads -> [] (never raises).
    assert parse_rankings_apps(None) == []
    assert parse_rankings_apps({}) == []
    assert parse_rankings_apps({"week": [None, "x", 5]}) == []


# (c) LOG PLACEMENT + VALLEY ------------------------------------------------ #
def test_log_placement_summit_floor_user_valley():
    # TEST_PLAN (c): summit=7.16e12 -> y≈1 (top); floor=71e9 -> y≈0 (bottom);
    # user=6.7e6 -> y BELOW the floor (NEGATIVE — the valley, not clamped equal).
    summit, floor, user = int(7.16e12), int(71e9), int(6.7e6)
    y_summit = log_y_frac(summit, floor, summit)
    y_floor = log_y_frac(floor, floor, summit)
    y_user = log_y_frac(user, floor, summit)
    assert y_summit == pytest.approx(1.0, abs=1e-9)
    assert y_floor == pytest.approx(0.0, abs=1e-9)
    assert y_user < 0.0                              # the valley, BELOW the floor
    assert y_user < y_floor                          # strictly below, not clamped


def test_log_placement_degenerate_single_rung():
    # floor == summit (a one-rung board) -> a safe 1.0 (no divide-by-zero).
    assert log_y_frac(5, 5, 5) == 1.0


def test_build_user_in_valley_and_gap():
    # The builder marks the user in the valley and computes the honest gap
    # multiple (floor / user) — a positive 'distance to the board', never a win.
    apps = parse_rankings_apps(_apps_week_stub())
    cc = build_court_climb(parse_task_classifications(_classifications_stub()),
                           apps, TOP_MODEL, USER_TOKENS, USER_APP)
    assert cc.user_in_valley is True
    assert cc.user_tokens == USER_TOKENS
    # floor ~73B / user ~6.7M ~= 10,000x (the headline magnitude).
    assert cc.gap_multiple > 5000
    assert cc.user_abbr == "6.70M"
    assert "to reach the board" in cc.gap_abbr
    assert cc.gap_abbr.startswith("~")


def test_thin_rungs_keeps_summit_and_floor():
    # 20 rungs thinned to ~6 (decision E): summit (#1) + floor (#20) ALWAYS kept,
    # an evenly sampled middle between.
    apps = parse_rankings_apps(_apps_week_stub())
    thin = _thin_rungs(apps, CLIMB_RUNG_TARGET)
    assert len(thin) == CLIMB_RUNG_TARGET
    assert thin[0].rank == 1                          # summit
    assert thin[-1].rank == 20                         # floor
    ranks = [a.rank for a in thin]
    assert ranks == sorted(ranks)                      # rank order preserved
    # a board already smaller than the target is returned whole.
    assert len(_thin_rungs(apps[:4], CLIMB_RUNG_TARGET)) == 4


def test_abbr_tokens_magnitudes():
    assert abbr_tokens(7_311_066_568_924) == "7.31T"
    assert abbr_tokens(73_272_973_755) == "73.3B"
    assert abbr_tokens(6_697_136) == "6.70M"
    assert abbr_tokens(9_258) == "9.3K"
    assert abbr_tokens(0) == "0"


def test_abbr_multiple_rounds_to_one_sig_fig():
    # The gap reads as a magnitude, not fake precision: 10941 -> '10,000'.
    assert _abbr_multiple(10941) == "10,000"
    assert _abbr_multiple(91500) == "90,000"
    assert _abbr_multiple(4.8) == "5"


# (d) HONEST-CLAIM GUARD (the critical no-fake gate) ------------------------ #
def _all_built_strings(cc):
    """Every user-facing string the build layer produces, flattened for the
    forbidden-phrase scan (the on-card cached strings + the derived captions)."""
    parts = [cc.gap_abbr, cc.user_abbr, cc.user_app]
    for s in cc.seats:
        parts += [s.label, s.world_name, s.world_model, s.ember_name, s.ember_model]
    for r in cc.rungs:
        parts += [r.title, r.tokens_abbr]
    parts.append(repr(cc))
    return " ".join(str(x) for x in parts).lower()


def test_honest_claim_guard_no_out_tokened_when_below_floor(qapp):
    # TEST_PLAN (d): when user < floor, NO code path emits 'out-tokened' — scan the
    # built strings AND both rendered dossiers' HTML (case-insensitive).
    from widgets import build_court_dossier_html, build_climb_dossier_html
    apps = parse_rankings_apps(_apps_week_stub())
    cc = build_court_climb(parse_task_classifications(_classifications_stub()),
                           apps, TOP_MODEL, USER_TOKENS, USER_APP)
    assert cc.user_in_valley is True                   # the precondition

    built = _all_built_strings(cc)
    assert FORBIDDEN_CLAIM not in built                # 'out-tokened' absent
    assert "out-tokened" not in built
    assert "out tokened" not in built

    court_html = build_court_dossier_html(cc, cc.task_board).lower()
    climb_html = build_climb_dossier_html(cc, cc.all_apps).lower()
    assert "out-tokened" not in court_html
    assert "out-tokened" not in climb_html
    # and the honest framing IS present (taste-vs-world + the distance).
    assert "you reach for" in court_html or "taste" in court_html
    assert "to reach the board" in climb_html
    # the climb NEVER claims a rank/beat for the user.
    assert "out-ranked" not in climb_html
    assert "you beat" not in climb_html


def test_honest_claim_guard_even_if_user_somehow_on_board():
    # Defensive: even if the user's tokens exceeded the floor (impossible today),
    # the build layer STILL never produces an 'out-tokened' claim.
    apps = parse_rankings_apps(_apps_week_stub())
    huge_user = 9_999_999_999_999                       # above the summit
    cc = build_court_climb(parse_task_classifications(_classifications_stub()),
                           apps, TOP_MODEL, huge_user, USER_APP)
    assert cc.user_in_valley is False
    built = _all_built_strings(cc)
    assert "out-tokened" not in built


# (e) WINDOW GUARD ---------------------------------------------------------- #
def test_get_task_classifications_always_sends_window_7d(monkeypatch):
    # TEST_PLAN (e): the client ALWAYS sends window=7d (any other value 400s live).
    from api_client import APIClient
    captured = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": _classifications_stub()}

    client = APIClient()

    def _fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(client.session, "get", _fake_get)
    board = client.get_task_classifications()
    assert captured["params"] == {"window": "7d"}       # HARDCODED 7d
    assert "/api/v1/classifications/task" in captured["url"]
    assert board is not None and not board.is_empty


def test_get_task_classifications_user_auth_session():
    # The classifications call rides the USER-auth session (NOT FrontendClient) —
    # the session carries the API_KEY Authorization header (user-key-gated).
    from api_client import APIClient
    client = APIClient()
    assert "Authorization" in client.session.headers


# (f) DEGRADE --------------------------------------------------------------- #
def test_degrade_classifications_none_court_unavailable_climb_builds():
    # TEST_PLAN (f) part 1: classifications=None -> court 'unavailable' flag, but
    # the climb (noauth) still builds with its rungs + valley marker.
    apps = parse_rankings_apps(_apps_week_stub())
    cc = build_court_climb(None, apps, TOP_MODEL, USER_TOKENS, USER_APP)
    assert cc.court_available is False
    assert cc.seats == ()
    assert cc.climb_available is True
    assert len(cc.rungs) == CLIMB_RUNG_TARGET
    assert cc.user_in_valley is True
    assert cc.is_empty is False                         # the climb still shows


def test_degrade_mgmt_locked_drops_ember_keeps_summit():
    # TEST_PLAN (f) part 2: mgmt LOCKED (top_model None, user_tokens 0) -> the
    # ember overlays are DROPPED (has_ember False, no seat carries an ember) but
    # the world court + the apps summit STILL render.
    apps = parse_rankings_apps(_apps_week_stub())
    cc = build_court_climb(parse_task_classifications(_classifications_stub()),
                           apps, None, 0, "")
    assert cc.has_ember is False
    assert all(not s.has_ember for s in cc.seats)       # no ember anywhere
    assert cc.court_available is True                    # the world court renders
    assert cc.climb_available is True
    assert cc.summit_tokens == 7311066568924             # the summit still shows
    assert cc.user_in_valley is False                    # no user marker (0 toks)


def test_degrade_both_sources_fail_is_empty():
    # Both halves unavailable -> is_empty (the widget's tidy slate).
    cc = build_court_climb(None, None, None, 0, "")
    assert cc.is_empty is True
    assert cc.court_available is False
    assert cc.climb_available is False


def test_ember_seated_on_default_agent_macro():
    # The ember chip is seated on the DEFAULT agent macro (decision C) — exactly
    # one seat carries it, and it's the agent seat.
    apps = parse_rankings_apps(_apps_week_stub())
    cc = build_court_climb(parse_task_classifications(_classifications_stub()),
                           apps, TOP_MODEL, USER_TOKENS, USER_APP)
    ember_seats = [s for s in cc.seats if s.has_ember]
    assert len(ember_seats) == 1
    assert ember_seats[0].macro == DEFAULT_EMBER_MACRO
    assert cc.ember_macro == DEFAULT_EMBER_MACRO
    # the ember is the user's TOP model, framed as taste (never a 'win').
    assert ember_seats[0].ember_model == TOP_MODEL


# =========================================================================== #
#  2. THE WIDGET (qapp) — TaskCourt                                            #
# =========================================================================== #
def _court(qapp, cc=None, width=316, anim_on=False):
    from widgets import TaskCourt
    import anim
    anim.set_enabled(anim_on)
    w = TaskCourt()
    w.resize(width, w.sizeHint().height() or 269)
    if cc is not None:
        w.set_data(cc)
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


def _full_cc():
    apps = parse_rankings_apps(_apps_week_stub())
    return build_court_climb(parse_task_classifications(_classifications_stub()),
                             apps, TOP_MODEL, USER_TOKENS, USER_APP)


def test_widget_renders_four_crowns_and_one_ember_chip(qapp):
    # The COURT lays out 4 seats (one per macro), with the ember chip on exactly
    # ONE seat (the agent seat); painting both bands does not raise.
    w = _court(qapp, _full_cc())
    assert w._cc is not None
    assert len(w._cc.seats) == 4
    assert len(w._seat_rects) == 4
    ember_seats = [s for s in w._cc.seats if s.has_ember]
    assert len(ember_seats) == 1 and ember_seats[0].macro == "agent"
    # the cached elided names: the ember seat's tuple carries a non-empty ember.
    agent_idx = [i for i, s in enumerate(w._cc.seats) if s.macro == "agent"][0]
    assert w._seat_names[agent_idx][1] != ""           # ember name cached
    code_idx = [i for i, s in enumerate(w._cc.seats) if s.macro == "code"][0]
    assert w._seat_names[code_idx][1] == ""            # no ember on code
    _grab(w)                                            # paints both bands


def test_widget_climb_user_marker_below_floor(qapp):
    # The CLIMB renders the rope ladder; the user ember marker RESTS in the valley
    # BELOW the floor rung's y (the honest base camp, NOT clamped onto the floor).
    w = _court(qapp, _full_cc())
    assert len(w._rung_layout) == CLIMB_RUNG_TARGET
    # the resting user y is strictly BELOW the floor rung y (larger y == lower).
    assert w._user_y_rest > w._floor_y
    # and within the climb band (not clipped off the bottom).
    band_bottom = w._climb_rect.y() + w.CLIMB_H
    assert w._user_y_rest <= band_bottom
    _grab(w)


def test_widget_ascent_fires_once_then_skips_same_data(qapp, monkeypatch):
    # The `ascent` climb-in START is gated behind a data-CHANGED check: it runs on
    # the FIRST populated set_data and is SKIPPED when the SAME data re-arrives.
    w = _court(qapp, anim_on=True)
    starts = []
    monkeypatch.setattr(w, "_start_ascent", lambda: starts.append(1))
    cc1 = _full_cc()
    w.set_data(cc1)
    assert len(starts) == 1                             # fired ONCE on first data
    # re-arrive with EQUAL data (a fresh equal payload) -> NO re-start (the gate).
    w.set_data(_full_cc())
    assert len(starts) == 1                             # still ONE — the gate held
    # a DIFFERENT payload (a different user total) -> the gate opens, fires again.
    apps = parse_rankings_apps(_apps_week_stub())
    cc3 = build_court_climb(parse_task_classifications(_classifications_stub()),
                            apps, TOP_MODEL, 5_000_000, USER_APP)
    w.set_data(cc3)
    assert len(starts) == 2


def test_widget_ascent_property_distinct_no_move(qapp):
    # `ascent` is a DISTINCT Property (NOT pos/size/geometry); setting it changes
    # only the ember-dot y, never the widget geometry (the no-move regression).
    w = _court(qapp, _full_cc())
    pos_before = (w.x(), w.y())
    geom_before = (w.width(), w.height())
    w.set_ascent(0.5)
    assert w.get_ascent() == pytest.approx(0.5)
    assert (w.x(), w.y()) == pos_before
    assert (w.width(), w.height()) == geom_before


def test_widget_anim_disabled_ascent_parks_at_one(qapp):
    # reduce-motion -> ascent parked at 1.0 (no running anim, marker at rest).
    w = _court(qapp, _full_cc(), anim_on=False)
    assert w.get_ascent() == pytest.approx(1.0)


def test_widget_court_unavailable_climb_still_renders(qapp):
    # classifications=None -> the court band collapses to 'world task board
    # unavailable' (court_available False) BUT the climb still renders its rungs.
    apps = parse_rankings_apps(_apps_week_stub())
    cc = build_court_climb(None, apps, TOP_MODEL, USER_TOKENS, USER_APP)
    w = _court(qapp, cc)
    assert w._cc.court_available is False
    assert w._cc.climb_available is True
    assert len(w._rung_layout) == CLIMB_RUNG_TARGET      # the ladder still there
    _grab(w)                                             # paints the slate + ladder


def test_widget_locked_drops_ember_keeps_height(qapp):
    # set_locked() -> the ember overlays dropped; the locked height MATCHES the
    # populated height so the section never jumps.
    w = _court(qapp, _full_cc())
    h_pop = w.sizeHint().height()
    assert w.height() == h_pop
    w.set_locked()
    assert w._locked is True
    assert w._cc is None
    assert w.sizeHint().height() == h_pop                # no jump locked vs pop
    _grab(w)


def test_widget_sizehint_stable_269(qapp):
    # The height is fixed by the two bands + the hairline (COURT 118 + 1 + CLIMB
    # 150 = 269) and is stable (one measure pass feeds paint + sizeHint).
    w = _court(qapp, _full_cc())
    assert w.sizeHint().height() == 118 + 1 + 150
    assert w.height() == 118 + 1 + 150


def test_widget_court_click_emits_court_clicked(qapp):
    # A click in the COURT band (top) emits court_clicked(anchor_y) -> the court
    # dossier; a click in the CLIMB band (bottom) emits climb_clicked.
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    w = _court(qapp, _full_cc())
    court_fired, climb_fired = [], []
    w.court_clicked.connect(lambda y: court_fired.append(y))
    w.climb_clicked.connect(lambda y: climb_fired.append(y))
    # a press in the COURT band (y < 118).
    ev_c = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(40, 60),
                       QPointF(40, 60), Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev_c)
    assert len(court_fired) == 1 and len(climb_fired) == 0
    # a press in the CLIMB band (y > 119).
    ev_m = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(40, 200),
                       QPointF(40, 200), Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev_m)
    assert len(climb_fired) == 1


def test_widget_locked_click_noop(qapp):
    # When locked, neither band emits (nothing to drill into).
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    w = _court(qapp, None)
    w.set_locked()
    fired = []
    w.court_clicked.connect(lambda y: fired.append(y))
    w.climb_clicked.connect(lambda y: fired.append(y))
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(40, 60),
                     QPointF(40, 60), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    w.mousePressEvent(ev)
    assert fired == []


# --- the dossiers (qapp — they render a pixmap) --------------------------- #
def test_court_dossier_html_complete_and_world_framed(qapp):
    # The court dossier carries the top-3 per macro (in the painted strip) + the
    # WORLD framing spelled out (taste-vs-world, NEVER the user's split).
    from widgets import build_court_dossier_html, CourtDossierStrip
    cc = _full_cc()
    html_str = build_court_dossier_html(cc, cc.task_board)
    assert "<img src='data:image/png;base64," in html_str   # the painted strip
    low = html_str.lower()
    assert "global market share" in low or "not your split" in low  # world framing
    assert "you reach for" in low                        # the ember taste line
    # the strip measures top-3 rows per macro.
    strip = CourtDossierStrip(cc, cc.task_board)
    assert len(strip._rows) == 4                          # one block per macro


def test_climb_dossier_html_full_ladder_user_at_foot(qapp):
    # The climb dossier carries the FULL 20-app ladder (in the painted strip) with
    # the user's row at the foot + the honest distance — NEVER an 'out-tokened'
    # claim.
    from widgets import build_climb_dossier_html, ClimbDossierStrip
    cc = _full_cc()
    html_str = build_climb_dossier_html(cc, cc.all_apps)
    assert "<img src='data:image/png;base64," in html_str
    low = html_str.lower()
    assert "to reach the board" in low                    # the honest distance
    assert "out-tokened" not in low
    assert "opencode" in low                              # the user's app label
    # the strip carries all 20 app rows (+ the user row painted at the foot).
    strip = ClimbDossierStrip(cc, cc.all_apps)
    assert len(strip._rows) == 20


def test_dossier_html_escapes_injection(qapp):
    # A hostile app title / model name can't break out of the HTML wrapper
    # (html.escape on the wrapper; the strip text is QPainter-drawn).
    from widgets import build_court_dossier_html, build_climb_dossier_html
    evil_apps = [AppRanking(rank=1, title="<script>alert(1)</script>",
                            slug="x", favicon_url="", total_tokens=10 ** 12,
                            total_requests=1)]
    evil_board = TaskBoard(
        crowns=(MacroCrown(macro="agent", label="Agentic",
                           world_model="<script>evil</script>", world_share=0.3,
                           model_share=0.3),),
        macro_models={"agent": [("<script>evil</script>", 0.3)]})
    cc = build_court_climb(evil_board, evil_apps,
                           "<script>m</script>", USER_TOKENS, "<b>OpenCode</b>")
    court_html = build_court_dossier_html(cc, cc.task_board)
    climb_html = build_climb_dossier_html(cc, cc.all_apps)
    assert "<script>" not in court_html
    assert "<script>" not in climb_html
    # the app label is escaped in the climb dossier wrapper text.
    assert "<b>OpenCode</b>" not in climb_html
    assert "&lt;b&gt;OpenCode&lt;/b&gt;" in climb_html
