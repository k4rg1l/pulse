"""#18 THE COURT & THE CLIMB — the pure compute layer (no Qt, no I/O).

The Insights zone's FOURTH widget (the wide closer, under #17): TWO stacked
honest bands fused into one heraldic story.

  THE COURT  — a four-seat royal court, one crowned model per macro task category
  (code/agent/data/general) from the WORLD's task board, with YOUR top model
  seated beside the world's pick as a single ember chip ("the world crowns
  deepseek-v4-flash for agentic; you reach for claude-4.6-sonnet").

  THE CLIMB  — a vertical LOG-scale rope ladder of the public apps leaderboard
  (Hermes ~7.3T at the summit) with YOUR ~6.7M-token marker honestly pinned in
  the VALLEY below the floor (~73B), climbing.

HONESTY-AS-DESIGN (decisions C/D — the whole point):
  * classifications/task is GLOBAL market-share, NOT the user's personal task
    split (scope params are ignored; analytics has no task dimension). EVERY
    court label is framed as "the world", never as the user's mix. The user's
    top model is shown as taste ("you reach for Y"), NEVER as the user "winning"
    a task — the user's picks are not the world's.
  * the user's ~6.7M weekly tokens vs the apps board floor (~73B) is ~10,000x
    BELOW. The string "out-tokened" (or "out-tokened app #N", or any claim the
    user beat an app) is NEVER produced anywhere — a unit test scans the built
    strings for the forbidden phrase. The climb plots the apps on a LOG scale and
    pins the user in the valley below the floor rung with a faint "~Nx to reach
    the board" connector.

Dependency-light on purpose (mirrors model_of_week / token_recorder / value_assay
/ spend_palette's no-QWidget discipline) so it imports cleanly into dashboard.py,
widgets.py, AND the tests without a cycle. The parser + the builder are PURE so
they unit-test against captured fixtures, never the live endpoint.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Humanizer reused from #16's pure layer (no Qt, no I/O) — keeps the court chip
# names consistent with the belt ('anthropic/claude-4.6-sonnet-...' ->
# 'Claude 4.6 Sonnet').
from model_of_week import humanize_model, provider_of

# The fixed macro order the court rail renders left->right. The API may return
# the macros in any order; we render this canonical order so the seats are stable
# poll-to-poll. Any macro the API adds beyond these is appended after.
MACRO_ORDER = ("code", "agent", "data", "general")

# Human seat captions (the API's macro 'label' is used when present, else these).
_MACRO_LABEL = {
    "code": "Code",
    "agent": "Agentic",
    "data": "Data",
    "general": "General",
}

# The single forbidden phrase (decision D). Kept here as the ONE source of truth
# so the guard test and the build layer agree. Lower-cased for a case-insensitive
# scan.
FORBIDDEN_CLAIM = "out-tokened"

# The macro seat the user's top model is seated beside by default (decision C).
# A judgment call — the recon gives NO per-task split for the user, so we anchor
# the ember to the agentic seat (the headline "you reach for Y" beat) and NEVER
# claim the user wins that task.
DEFAULT_EMBER_MACRO = "agent"

# The climb thins the 20 rungs to ~this many for legibility at ~316px (decision
# E): the summit + an evenly sampled middle + the floor.
CLIMB_RUNG_TARGET = 6


# ===========================================================================
#  Parsed WORLD task board (from /api/v1/classifications/task?window=7d)
# ===========================================================================
@dataclass(frozen=True)
class MacroCrown:
    """One macro-category's world crown: the top MODEL across that macro's tasks
    plus the macro's overall token share (the gold underline width).

    macro          : the macro key ('agent').
    label          : a human seat caption ('Agentic').
    world_model    : the crowned model id (max aggregated tag_token_share), or ''.
    world_share    : the macro's token_share of ALL OpenRouter traffic (0..1) —
                     drives the gold underline length (the world's appetite for
                     that task), NOT the model's within-macro share.
    model_share    : the crowned model's aggregated within-macro tag_token_share
                     (0..1) — shown in the dossier, never as a personal stat.
    """
    macro: str = ""
    label: str = ""
    world_model: str = ""
    world_share: float = 0.0
    model_share: float = 0.0

    @property
    def world_model_name(self) -> str:
        return humanize_model(self.world_model) if self.world_model else ""


@dataclass(frozen=True)
class TaskBoard:
    """The parsed WORLD task board — the per-macro crowns + the top-3 models per
    macro (for the dossier) + the macro shares table. GLOBAL market-share, framed
    as 'the world' (decision C)."""
    crowns: Tuple[MacroCrown, ...] = ()
    as_of: str = ""
    window_days: int = 7
    # macro -> [(model_id, within_macro_share), ...] descending (dossier top-3+).
    macro_models: dict = field(default_factory=dict)

    def crown_for(self, macro: str) -> Optional[MacroCrown]:
        for c in self.crowns:
            if c.macro == macro:
                return c
        return None

    @property
    def is_empty(self) -> bool:
        return not self.crowns


def parse_task_classifications(data) -> TaskBoard:
    """Build a :class:`TaskBoard` from a ``classifications/task`` payload. Pure.

    ``data`` is the inner ``{window_days, as_of, classifications:[...],
    macro_categories:[...]}`` dict. For each macro:
      - the crown = the model with the max SUM of ``tag_token_share`` across all
        of that macro's classification rows (a model spread over several tasks in
        the macro aggregates — this is the world's go-to model for the macro).
      - ``world_share`` = the macro_category's ``token_share`` (the world's share
        of ALL traffic spent on that macro) — the gold underline width.
    Returns an empty board on a missing/odd payload (never raises)."""
    if not isinstance(data, dict):
        return TaskBoard()
    classifications = data.get("classifications") or []
    macros = data.get("macro_categories") or []

    # macro -> {model_id: aggregated tag_token_share}
    agg: dict = {}
    for c in classifications:
        if not isinstance(c, dict):
            continue
        mk = c.get("macro_category")
        if not mk:
            continue
        bucket = agg.setdefault(mk, {})
        for mod in (c.get("models") or []):
            if not isinstance(mod, dict):
                continue
            mid = mod.get("id") or mod.get("tag")
            if not mid:
                continue
            try:
                sh = float(mod.get("tag_token_share") or 0.0)
            except (TypeError, ValueError):
                sh = 0.0
            bucket[mid] = bucket.get(mid, 0.0) + sh

    # macro -> token_share (the world's appetite for that macro)
    macro_share: dict = {}
    macro_label: dict = {}
    for m in macros:
        if not isinstance(m, dict):
            continue
        mk = m.get("key")
        if not mk:
            continue
        try:
            macro_share[mk] = float(m.get("token_share") or 0.0)
        except (TypeError, ValueError):
            macro_share[mk] = 0.0
        macro_label[mk] = m.get("label") or _MACRO_LABEL.get(mk, mk.capitalize())

    # Canonical seat order: MACRO_ORDER first, then any extra macros the API has.
    seen = []
    for mk in MACRO_ORDER:
        if mk in agg or mk in macro_share:
            seen.append(mk)
    for mk in list(agg.keys()) + list(macro_share.keys()):
        if mk not in seen:
            seen.append(mk)

    crowns = []
    macro_models: dict = {}
    for mk in seen:
        models = agg.get(mk, {})
        ranked = sorted(models.items(), key=lambda kv: (-kv[1], str(kv[0])))
        macro_models[mk] = ranked
        top_id, top_share = (ranked[0] if ranked else ("", 0.0))
        crowns.append(MacroCrown(
            macro=mk,
            label=macro_label.get(mk, _MACRO_LABEL.get(mk, mk.capitalize())),
            world_model=top_id,
            world_share=macro_share.get(mk, 0.0),
            model_share=top_share,
        ))

    win = data.get("window_days")
    try:
        win = int(win) if win is not None else 7
    except (TypeError, ValueError):
        win = 7

    return TaskBoard(
        crowns=tuple(crowns),
        as_of=str(data.get("as_of") or ""),
        window_days=win,
        macro_models=macro_models,
    )


# ===========================================================================
#  The render payload — the four seats + the climb + the degrade flags
# ===========================================================================
@dataclass(frozen=True)
class CourtSeat:
    """One throne-seat in the court (render-ready).

    macro        : the macro key.
    label        : the human seat caption ('Agentic').
    world_model  : the crowned model id (the gold crown + colored chip), or ''.
    world_name   : the humanized crowned-model name.
    world_share  : the macro's world token_share (0..1) — gold underline width.
    ember_model  : the user's top model id seated here as an EMBER chip, or '' —
                   present on exactly ONE seat (the ember seat) and ONLY when the
                   user's mgmt top model is known (decision C). NEVER a 'win'.
    ember_name   : the humanized ember-model name.
    """
    macro: str = ""
    label: str = ""
    world_model: str = ""
    world_name: str = ""
    world_share: float = 0.0
    ember_model: str = ""
    ember_name: str = ""

    @property
    def has_ember(self) -> bool:
        return bool(self.ember_model)


@dataclass(frozen=True)
class ClimbRung:
    """One rung on the apps ladder (render-ready).

    rank      : the app's public rank (#1 = the summit).
    title     : the app title (elided + favicon-dot on the rung).
    slug      : the app slug.
    favicon_url : the app favicon (best-effort dot), or ''.
    tokens    : the app's weekly total_tokens (int).
    tokens_abbr : the cached abbreviation ('7.31T') — formatted ONCE here, never
                  in the paint hot path.
    y_frac    : the LOG-scale vertical position, 0.0 (floor) .. 1.0 (summit). The
                widget maps this to pixels.
    is_floor  : True for the lowest plotted rung (the connector anchor).
    """
    rank: int = 0
    title: str = ""
    slug: str = ""
    favicon_url: str = ""
    tokens: int = 0
    tokens_abbr: str = ""
    y_frac: float = 0.0
    is_floor: bool = False


@dataclass(frozen=True)
class CourtClimb:
    """The whole #18 widget payload: the court seats + the climb rungs + the
    user's honest valley marker + the per-half degrade flags.

    seats          : the four (or fewer) CourtSeats, canonical macro order.
    court_available: True when the WORLD task board parsed (decision E — when
                     False the court band collapses to 'world task board
                     unavailable' and ONLY the climb renders).
    has_ember      : True when the user's mgmt top model is known and seated as an
                     ember chip on the ember seat (decision E — when False the
                     ember overlays are DROPPED and a 'connect a key to place
                     yourself' note shows; the world court + the summit still
                     render).
    ember_macro    : the macro key the ember chip is seated on ('agent').

    rungs          : the thinned (~6) apps ladder rungs, summit-first.
    summit_tokens  : the #1 app's tokens (the ladder top reference).
    floor_tokens   : the lowest-plotted rung's tokens (the connector anchor).
    user_tokens    : the user's weekly token total (~6.7M) — the valley marker.
    user_abbr      : the cached '6.70M' abbreviation.
    user_app       : the user's app label ('OpenCode'), or ''.
    gap_multiple   : floor_tokens / user_tokens (how many x to reach the board) —
                     a POSITIVE multiple ONLY when the user is BELOW the floor;
                     the honest distance, never an 'out-tokened' claim.
    user_in_valley : True when user_tokens < floor_tokens (the user sits below the
                     lowest rung — the honest state today). When somehow False the
                     widget would place the user ON the ladder, but the guard test
                     asserts the forbidden claim is still never produced.
    climb_available: True when the apps ladder parsed (NOAUTH -> almost always).
    """
    seats: Tuple[CourtSeat, ...] = ()
    court_available: bool = False
    has_ember: bool = False
    ember_macro: str = ""

    rungs: Tuple[ClimbRung, ...] = ()
    summit_tokens: int = 0
    floor_tokens: int = 0
    user_tokens: int = 0
    user_abbr: str = ""
    user_app: str = ""
    gap_multiple: float = 0.0
    user_in_valley: bool = False
    climb_available: bool = False

    # -- dossier sources (carried so the widget's tap-through is self-contained;
    #    the on-card paint never reads these) --
    task_board: Optional[object] = None   # the full TaskBoard (top-3 per macro)
    all_apps: tuple = ()                   # the full top-20 AppRanking list

    @property
    def is_empty(self) -> bool:
        """Nothing to show on EITHER half (both sources failed) -> the widget's
        tidy 'unavailable' slate. The climb is noauth so this is rare."""
        return not self.court_available and not self.climb_available

    @property
    def gap_abbr(self) -> str:
        """A compact magnitude for the gap connector ('~10,000x', '~90,000x').
        Empty when there's no honest below-the-floor gap to show."""
        if not self.user_in_valley or self.gap_multiple < 1.0:
            return ""
        return "~" + _abbr_multiple(self.gap_multiple) + "x to reach the board"


# ===========================================================================
#  Helpers (token abbreviation + log placement) — PURE, cached into the
#  dataclass strings in build_court_climb (never the paint hot path).
# ===========================================================================
def abbr_tokens(n: int) -> str:
    """A compact token magnitude for a rung / the valley marker.
    7311066568924 -> '7.31T'; 73272973755 -> '73.3B'; 6697136 -> '6.70M'."""
    n = int(n or 0)
    if n >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.2f}T"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n}"


def _abbr_multiple(x: float) -> str:
    """A rounded human multiple for the gap connector. 91500 -> '90,000';
    10000 -> '10,000'; 4.8 -> '5'. Rounds to one significant-ish figure so the
    delight reads as a magnitude, not a fake-precise number."""
    x = float(x or 0.0)
    if x <= 0:
        return "0"
    if x < 10:
        return f"{x:.0f}"
    # Round to 1 significant figure, then group with commas.
    mag = 10 ** int(math.floor(math.log10(x)))
    rounded = int(round(x / mag) * mag)
    return f"{rounded:,}"


def log_y_frac(tokens: int, floor: int, summit: int) -> float:
    """The LOG-scale vertical fraction for a token count, 0.0 (floor) .. 1.0
    (summit): y = (log10(t) - log10(floor)) / (log10(summit) - log10(floor)).

    Used for BOTH the rungs and the user marker. For a rung between floor and
    summit this lands in [0, 1]. For the user (below the floor) it goes NEGATIVE
    (the valley) — the widget does NOT clamp it onto the floor (decision D); it
    plots the user below the lowest rung. Guards a degenerate floor==summit (one
    rung) by returning 1.0."""
    t = max(1, int(tokens or 0))
    fl = max(1, int(floor or 0))
    su = max(1, int(summit or 0))
    lt, lf, ls = math.log10(t), math.log10(fl), math.log10(su)
    span = ls - lf
    if span <= 0:
        return 1.0
    return (lt - lf) / span


def _thin_rungs(apps: list, target: int) -> list:
    """Pick ~target apps to plot for legibility (decision E): ALWAYS the summit
    (#1) + the floor (last) + an evenly sampled middle. Preserves rank order and
    never duplicates. ``apps`` is the descending-rank AppRanking list."""
    n = len(apps)
    if n <= target:
        return list(apps)
    # Always keep index 0 (summit) and n-1 (floor); sample the rest evenly.
    keep_idx = {0, n - 1}
    inner_slots = target - 2
    if inner_slots > 0:
        step = (n - 1) / (inner_slots + 1)
        for i in range(1, inner_slots + 1):
            keep_idx.add(int(round(i * step)))
    return [apps[i] for i in sorted(keep_idx)]


# ===========================================================================
#  The pure builder
# ===========================================================================
def build_court_climb(task_board,
                      apps: Optional[list],
                      top_model: Optional[str],
                      user_tokens: int,
                      user_app: str = "",
                      ember_macro: str = DEFAULT_EMBER_MACRO) -> CourtClimb:
    """Pure builder: fuse the WORLD task board + the apps ladder + the user's
    mgmt facts into ONE render payload, honoring every honesty gate.

    task_board : a TaskBoard (from parse_task_classifications) or None (the court
                 half degrades to 'unavailable' — court_available False).
    apps       : a list of AppRanking (from parse_rankings_apps) or None/[] (the
                 climb half can't render — climb_available False; this is rare,
                 the board is noauth).
    top_model  : the user's mgmt top model id (#16's cache) or None — the ember
                 overlay needs it (has_ember False when absent -> ember dropped,
                 'connect a key' note).
    user_tokens: the user's weekly token total (#17's cache; ~6.7M today).
    user_app   : the user's app label (analytics dims=[app]; 'OpenCode').
    ember_macro: which macro seat the ember chip is seated on (default 'agent').

    Contract (decisions C/D/E):
      - the crown per macro = the world's top model (already chosen in the parse).
      - the ember chip is seated on ONE seat only and ONLY when top_model is set;
        it is taste ('you reach for Y'), NEVER a 'win'.
      - the climb is a LOG-scale ladder; the user marker sits BELOW the floor (in
        the valley) when user_tokens < floor; gap_multiple = floor/user.
      - NO 'out-tokened' string is ever produced (no code path emits it).
    Never raises."""
    # ---- THE COURT (the world's crowns + the single ember overlay) ----------
    court_available = (task_board is not None and not task_board.is_empty)
    has_ember = bool(top_model)
    seats: List[CourtSeat] = []
    if court_available:
        # The ember rides the requested macro if that seat exists, else the first
        # seat (never blank when we have a top model + a court).
        macro_keys = [c.macro for c in task_board.crowns]
        ember_seat_macro = ember_macro if ember_macro in macro_keys else (
            macro_keys[0] if macro_keys else "")
        for c in task_board.crowns:
            is_ember_seat = has_ember and c.macro == ember_seat_macro
            seats.append(CourtSeat(
                macro=c.macro,
                label=c.label,
                world_model=c.world_model,
                world_name=c.world_model_name,
                world_share=c.world_share,
                ember_model=(top_model if is_ember_seat else ""),
                ember_name=(humanize_model(top_model) if is_ember_seat else ""),
            ))
        used_macro = ember_seat_macro if has_ember else ""
    else:
        used_macro = ""

    # ---- THE CLIMB (the log-scale apps ladder + the honest valley marker) ----
    apps = apps or []
    climb_available = bool(apps)
    rungs: List[ClimbRung] = []
    summit_tokens = 0
    floor_tokens = 0
    user_tokens = int(user_tokens or 0)
    if climb_available:
        # Descending by tokens so the summit is index 0 (the API returns rank
        # order; re-sort defensively so y-placement is monotone).
        ordered = sorted(apps, key=lambda a: -int(getattr(a, "total_tokens", 0)))
        summit_tokens = int(getattr(ordered[0], "total_tokens", 0))
        floor_tokens = int(getattr(ordered[-1], "total_tokens", 0))
        plotted = _thin_rungs(ordered, CLIMB_RUNG_TARGET)
        last_idx = len(plotted) - 1
        for i, a in enumerate(plotted):
            tok = int(getattr(a, "total_tokens", 0))
            rungs.append(ClimbRung(
                rank=int(getattr(a, "rank", 0)),
                title=getattr(a, "title", "") or getattr(a, "slug", "") or "",
                slug=getattr(a, "slug", "") or "",
                favicon_url=getattr(a, "favicon_url", "") or "",
                tokens=tok,
                tokens_abbr=abbr_tokens(tok),
                y_frac=log_y_frac(tok, floor_tokens, summit_tokens),
                is_floor=(i == last_idx),
            ))

    user_in_valley = climb_available and user_tokens > 0 and user_tokens < floor_tokens
    gap_multiple = (floor_tokens / user_tokens) if (user_in_valley and user_tokens > 0) else 0.0

    return CourtClimb(
        seats=tuple(seats),
        court_available=court_available,
        has_ember=has_ember,
        ember_macro=used_macro,
        rungs=tuple(rungs),
        summit_tokens=summit_tokens,
        floor_tokens=floor_tokens,
        user_tokens=user_tokens,
        user_abbr=abbr_tokens(user_tokens) if user_tokens > 0 else "",
        user_app=user_app or "",
        gap_multiple=gap_multiple,
        user_in_valley=user_in_valley,
        climb_available=climb_available,
        task_board=(task_board if court_available else None),
        all_apps=tuple(apps),
    )
