"""
OpenRouter Pulse — Frontend (no-auth) API client  ·  foundation F2

A thin wrapper over ``openrouter.ai/api/frontend/*`` — the undocumented website
API that powers OpenRouter's own model pages. NO auth, NO key budget; it works
for every user. See docs/OPENROUTER-RESEARCH.md "Tier C" for the full map.

Gotchas, all hit live (re-verified 2026-06-23) and baked into the methods here:

* The ``/v1/`` path segment is required for ``stats/*`` and ``rankings/*`` — but
  NOT for ``all-providers``, which lives at the bare ``/api/frontend/all-providers``.
* ``stats/*`` want the **versioned permaslug** (``anthropic/claude-4.8-opus-20260528``),
  never the public slug (``anthropic/claude-opus-4.8``). Resolve via
  ``catalog/models`` — that's what :class:`PermaslugResolver` is for.
* ``stats/uptime-hourly`` wants ``id=<endpoint-UUID>`` (from ``stats/endpoint``),
  not a permaslug.
* A browser-ish ``User-Agent`` is sent; some edges 403 a bare python UA.

Every payload is parsed by a PURE ``parse_*`` function so it can be unit-tested
against a captured fixture, never the live endpoint (the Sources contract).
"""
import logging
import re
import requests
from dataclasses import dataclass, field
from typing import Optional

from num import as_float, as_int

log = logging.getLogger("pulse.frontend")

BASE_URL = "https://openrouter.ai"
# A real-ish UA — the site 403s some bare python user-agents on stats/* edges.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Pulse/1.0"


def _norm(s: str) -> str:
    """Lowercase, reduce to space-separated alphanumerics. So the slug
    'amazon-bedrock' and the display name 'Amazon Bedrock' both collapse to
    'amazon bedrock' — letting us cross-match a board provider (which only
    carries a display name + a 'slug/region' tag) to an all-providers row."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def provider_slug_from_tag(tag: str) -> str:
    """The public ``/models/{id}/endpoints`` rows carry a ``tag`` shaped like
    ``'amazon-bedrock/eu-west-1'`` — the part before the first '/' is the
    provider slug, which matches all-providers' ``slug``. Region tag-less
    providers (``'anthropic'``) return the whole string."""
    if not tag:
        return ""
    return tag.split("/", 1)[0]


# ---------------------------------------------------------------------------
#  slug ↔ permaslug resolver  (from /api/frontend/v1/catalog/models)
# ---------------------------------------------------------------------------
class PermaslugResolver:
    """Two-way map between the public slug (``anthropic/claude-opus-4.8``) and
    the versioned permaslug (``anthropic/claude-4.8-opus-20260528``) that the
    ``stats/*`` endpoints require. Built once from the catalog and cached."""

    def __init__(self, slug_to_perma: dict):
        self._s2p = dict(slug_to_perma)
        # Last writer wins on permaslug collisions (shouldn't happen).
        self._p2s = {v: k for k, v in self._s2p.items()}

    def __len__(self):
        return len(self._s2p)

    def permaslug(self, slug: str) -> Optional[str]:
        """Public slug → versioned permaslug. Falls through to the slug itself
        if it already looks like a permaslug we know."""
        if slug in self._s2p:
            return self._s2p[slug]
        if slug in self._p2s:          # caller passed a permaslug already
            return slug
        return None

    def slug(self, permaslug: str) -> Optional[str]:
        return self._p2s.get(permaslug)


def parse_catalog_permaslugs(rows: list) -> PermaslugResolver:
    """Build a :class:`PermaslugResolver` from ``catalog/models`` rows. Pure."""
    s2p = {}
    for m in rows or []:
        if not isinstance(m, dict):
            continue
        slug = m.get("slug")
        perma = m.get("permaslug")
        if slug and perma:
            s2p[slug] = perma
    return PermaslugResolver(s2p)


# ---------------------------------------------------------------------------
#  Provider trust / privacy  (from /api/frontend/all-providers)
# ---------------------------------------------------------------------------
@dataclass
class ProviderTrust:
    """One provider's privacy posture + jurisdiction + logo, distilled from
    ``all-providers``. The computed trust *grade* lives in the #2 feature
    layer; this is just the faithful data."""
    slug: str = ""
    name: str = ""
    # --- data policy (what happens to YOUR prompt) ---
    trains: bool = False             # trains models on your prompts (worst)
    trains_openrouter: bool = False  # shares back to OpenRouter for training
    retains: bool = False            # keeps prompts at rest
    retention_days: Optional[int] = None
    can_publish: bool = False        # may publish your prompts publicly
    requires_user_ids: bool = False  # must be sent an end-user id
    has_policy: bool = True          # False when the provider reported none
    # --- jurisdiction ---
    headquarters: Optional[str] = None      # ISO country code (legal home)
    datacenters: tuple = ()                 # ISO codes where compute runs
    datacenters_known: bool = False         # False when the API returned null
    # --- branding ---
    icon_url: Optional[str] = None          # may be relative ('/images/…') or absolute
    byok_enabled: bool = False

    @property
    def icon_abs_url(self) -> Optional[str]:
        """Absolute logo URL (relative paths are rooted at openrouter.ai)."""
        if not self.icon_url:
            return None
        if self.icon_url.startswith("//"):
            return "https:" + self.icon_url
        if self.icon_url.startswith("/"):
            return BASE_URL + self.icon_url
        return self.icon_url


class ProviderTrustBook:
    """Lookup of provider → :class:`ProviderTrust`, matchable by slug (exact or
    normalized) or by display name (normalized). The board's per-provider rows
    only carry a display name + a ``slug/region`` tag, so we index every angle."""

    def __init__(self, providers: list):
        self._by_slug = {}
        self._by_norm = {}
        for p in providers:
            if p.slug:
                self._by_slug[p.slug] = p
                self._by_norm.setdefault(_norm(p.slug), p)
            if p.name:
                self._by_norm.setdefault(_norm(p.name), p)
        self._all = list(providers)

    def __len__(self):
        return len(self._all)

    def all(self) -> list:
        return list(self._all)

    def lookup(self, name: Optional[str] = None, slug: Optional[str] = None,
               tag: Optional[str] = None) -> Optional[ProviderTrust]:
        """Find a provider. Tries, in order: the raw tag's slug, the explicit
        slug (exact then normalized), then the normalized display name."""
        candidates = []
        if tag:
            candidates.append(provider_slug_from_tag(tag))
        if slug:
            candidates.append(slug)
        for c in candidates:
            if c in self._by_slug:
                return self._by_slug[c]
        for c in candidates:
            hit = self._by_norm.get(_norm(c))
            if hit:
                return hit
        if name:
            hit = self._by_norm.get(_norm(name))
            if hit:
                return hit
        return None


def parse_all_providers(rows: list) -> ProviderTrustBook:
    """Build a :class:`ProviderTrustBook` from ``all-providers`` rows. Pure."""
    providers = []
    for p in rows or []:
        if not isinstance(p, dict):
            continue
        dp = p.get("dataPolicy")
        has_policy = isinstance(dp, dict)
        dp = dp or {}
        raw_dcs = p.get("datacenters")
        dcs_known = isinstance(raw_dcs, list)
        dcs = raw_dcs if dcs_known else []
        icon = p.get("icon") or {}
        rd = dp.get("retentionDays")
        try:
            rd = int(rd) if rd is not None else None
        except (TypeError, ValueError):
            rd = None
        providers.append(ProviderTrust(
            slug=p.get("slug", "") or "",
            name=p.get("name") or p.get("displayName") or "",
            trains=bool(dp.get("training")),
            trains_openrouter=bool(dp.get("trainingOpenRouter")),
            retains=bool(dp.get("retainsPrompts")),
            retention_days=rd,
            can_publish=bool(dp.get("canPublish")),
            requires_user_ids=bool(dp.get("requiresUserIDs")),
            has_policy=has_policy,
            headquarters=(p.get("headquarters") or None),
            datacenters=tuple(str(c) for c in dcs),
            datacenters_known=dcs_known,
            icon_url=(icon.get("url") if isinstance(icon, dict) else None) or None,
            byok_enabled=bool(p.get("byokEnabled")),
        ))
    return ProviderTrustBook(providers)


# ---------------------------------------------------------------------------
#  The Ledger — a computed "Custody Score" + trust grade   (feature #2)
#
#  OpenRouter publishes each provider's raw data policy + jurisdiction, but no
#  single trust signal. So we compute one ourselves — exactly as The Arena
#  computes model ranks the API doesn't give: a 0–100 "Custody Score" (how safe
#  is YOUR prompt in this provider's hands), distilled to a letter grade S→F
#  with an itemized, auditable rap sheet. Training on your prompts is the
#  cardinal sin — a hard cap floors any trainer at F no matter how clean the
#  rest of its record. Jurisdiction is a bounded modifier, never the hero.
# ---------------------------------------------------------------------------

# (grade, lo, hi, hex), best→worst. S earns the Arena-style shimmer.
TRUST_TIERS = [
    ("S", 97, 100, "#7cf5c4"),
    ("A", 88, 96,  "#4ade80"),
    ("B", 76, 87,  "#a9d970"),
    ("C", 60, 75,  "#facc15"),
    ("D", 40, 59,  "#e8964a"),
    ("F", 0,  39,  "#f87171"),
]
TRUST_TOP = "S"

# Jurisdiction tax: legal homes with weaker prompt-privacy guarantees. Kept
# deliberately small + explicit so the penalty is auditable, not a black box.
_ADVERSE_HQ = {"CN"}
_NEUTRAL_HQ = {"IL", "ID"}


@dataclass
class Penalty:
    """One line on the rap sheet."""
    label: str
    delta: int               # always negative
    offense: bool = False    # an *active harm* → becomes a board emblem notch


@dataclass
class CustodyGrade:
    score: int = 100
    grade: str = "S"
    color: str = TRUST_TIERS[0][3]
    penalties: list = field(default_factory=list)   # Penalty, worst-first
    positives: list = field(default_factory=list)    # clean-bill checks (str)
    capped: bool = False                             # training hard-cap fired

    @property
    def is_top(self) -> bool:
        return self.grade == TRUST_TOP

    @property
    def offenses(self) -> list:
        return [p for p in self.penalties if p.offense]

    @property
    def notch_count(self) -> int:
        """Active-harm count for the seal's rim notches (capped at 4)."""
        return min(4, len(self.offenses))


def _trust_band(score: int):
    for grade, lo, hi, hexc in TRUST_TIERS:
        if lo <= score <= hi:
            return grade, hexc
    return TRUST_TIERS[-1][0], TRUST_TIERS[-1][3]


def custody_score(p: ProviderTrust) -> CustodyGrade:
    """Pure: distill a :class:`ProviderTrust` into a :class:`CustodyGrade`.
    Start at 100, subtract itemized penalties, clamp to 0–100, then apply the
    training hard cap. Deterministic and fully unit-testable."""
    score = 100
    penalties = []
    positives = []

    if p.trains:
        penalties.append(Penalty("Trains on your prompts", -45, offense=True))
        score -= 45
    else:
        positives.append("Never trains on your prompts")

    if p.can_publish:
        penalties.append(Penalty("Can publish your prompts", -25, offense=True))
        score -= 25
    else:
        positives.append("Cannot publish your prompts")

    if p.trains_openrouter:
        penalties.append(Penalty("Shares prompts to train OpenRouter", -10))
        score -= 10

    if p.retains:
        d = p.retention_days or 0
        delta = -min(20, round(d / 5)) if d else -8
        label = f"Retains prompts {d} days" if d else "Retains prompts (term undisclosed)"
        penalties.append(Penalty(label, delta, offense=bool(d)))
        score += delta
    else:
        positives.append("Zero prompt retention")

    if p.requires_user_ids:
        penalties.append(Penalty("Requires end-user IDs", -8))
        score -= 8

    # --- jurisdiction tax (bounded modifier) ---
    hq = p.headquarters
    if hq in _ADVERSE_HQ:
        penalties.append(Penalty(f"HQ in {hq} · weak prompt-privacy law", -12))
        score -= 12
    elif hq in _NEUTRAL_HQ:
        penalties.append(Penalty(f"HQ in {hq}", -6))
        score -= 6

    if p.datacenters and any(c != hq for c in p.datacenters):
        penalties.append(Penalty("Compute crosses a border", -6, offense=True))
        score -= 6
    elif not p.datacenters_known:
        penalties.append(Penalty("Datacenter location undisclosed", -4))
        score -= 4

    score = max(0, min(100, score))

    # --- the cardinal sin: training hard-caps you into F ---
    capped = False
    if p.trains and score > 39:
        score = 39
        capped = True
    grade, color = _trust_band(score)

    penalties.sort(key=lambda x: x.delta)   # worst (most negative) first
    return CustodyGrade(score=score, grade=grade, color=color,
                        penalties=penalties, positives=positives, capped=capped)


# ---------------------------------------------------------------------------
#  Speed leaderboard  (from /api/frontend/v1/rankings/performance)
# ---------------------------------------------------------------------------
@dataclass
class SpeedRanking:
    """One model's fleet-wide speed line (keyed by permaslug)."""
    permaslug: str = ""
    name: str = ""
    p50_throughput: Optional[float] = None   # tokens/sec (higher = faster)
    p50_latency: Optional[float] = None       # ms to first token (lower = faster)
    best_throughput_provider: Optional[str] = None
    best_throughput_price: Optional[float] = None
    best_latency_provider: Optional[str] = None
    best_latency_price: Optional[float] = None
    provider_count: int = 0
    request_count: int = 0


# --- #4 feature layer: a fleet-relative "velocity tier" + a render-ready standing ---
#
# OpenRouter ranks raw p50 throughput/latency but exposes no single "how fast is
# this, really?" signal. So — exactly as The Arena computes ranks and The Ledger
# computes a custody grade — we distill a model's place in the whole ranked field
# into a velocity TIER (a word + color) plus a render-ready :class:`SpeedStanding`.
# Throughput (stream speed) is the hero axis; latency (time-to-first-token) is the
# second story the two percentiles deliberately tell apart.

# (label, min fraction-of-field-beaten, hex), fastest→slowest. WARP earns the
# Arena-style shimmer. The palette is an "afterburner" ramp — intentionally
# distinct from the Arena's pinks/purples and the Ledger's green→red grades.
SPEED_TIERS = [
    ("WARP",    0.92, "#caa6ff"),   # plasma violet — top of the field
    ("BLAZING", 0.75, "#ff8a5c"),   # flame
    ("SWIFT",   0.55, "#ffc24b"),   # gold
    ("BRISK",   0.35, "#54d6b0"),   # mint
    ("STEADY",  0.15, "#6aa9e0"),   # steel blue
    ("IDLING",  0.00, "#8a8aa6"),   # grey
]
SPEED_ELITE = {"WARP"}


def speed_tier(pct: Optional[float]):
    """(label, hex) for a throughput percentile (fraction of the field beaten)."""
    if pct is None:
        return ("UNRANKED", "#64648c")
    for label, thr, hexc in SPEED_TIERS:
        if pct >= thr:
            return (label, hexc)
    return SPEED_TIERS[-1][0], SPEED_TIERS[-1][2]


@dataclass
class SpeedStanding:
    """One model's render-ready place in the speed fleet: the raw ranking plus
    both fleet-relative percentiles and integer ranks. Built by
    :meth:`SpeedBoard.standing` so all the relative math stays pure + testable."""
    ranking: SpeedRanking
    throughput_pct: Optional[float] = None   # fraction of field beaten (0..1)
    latency_pct: Optional[float] = None
    throughput_rank: Optional[int] = None    # 1 = fastest stream
    latency_rank: Optional[int] = None       # 1 = fastest first-token
    field_size: int = 0                       # ranked models carrying a throughput

    @property
    def permaslug(self) -> str:
        return self.ranking.permaslug

    @property
    def tier(self):
        return speed_tier(self.throughput_pct)

    @property
    def is_elite(self) -> bool:
        return self.tier[0] in SPEED_ELITE


class SpeedBoard:
    """The whole performance fleet, with self-relative percentiles computed
    against every other ranked model (that's the 'vs the field' in #4)."""

    def __init__(self, rankings: list):
        self._all = list(rankings)
        self._by_perma = {r.permaslug: r for r in rankings if r.permaslug}

    def __len__(self):
        return len(self._all)

    def all(self) -> list:
        return list(self._all)

    def lookup(self, permaslug: str) -> Optional[SpeedRanking]:
        return self._by_perma.get(permaslug)

    def _percentile(self, values: list, mine, higher_is_faster: bool) -> Optional[float]:
        """Fraction of the *rest of the field* this value beats (0..1). For
        throughput, beating = strictly greater; for latency, beating = strictly
        less. None when we can't place it (no value, or a field of one)."""
        if mine is None:
            return None
        others = [v for v in values if v is not None]
        # remove exactly one occurrence of `mine` (self) from the field
        if mine in others:
            others.remove(mine)
        if not others:
            return None
        if higher_is_faster:
            beaten = sum(1 for v in others if v < mine)
        else:
            beaten = sum(1 for v in others if v > mine)
        return beaten / len(others)

    def throughput_percentile(self, permaslug: str) -> Optional[float]:
        r = self._by_perma.get(permaslug)
        if r is None:
            return None
        return self._percentile(
            [x.p50_throughput for x in self._all], r.p50_throughput, True)

    def latency_percentile(self, permaslug: str) -> Optional[float]:
        r = self._by_perma.get(permaslug)
        if r is None:
            return None
        return self._percentile(
            [x.p50_latency for x in self._all], r.p50_latency, False)

    def _rank(self, values: list, mine, higher_is_faster: bool) -> Optional[int]:
        """1-based rank of `mine` in the whole field (1 = fastest). For
        throughput, faster = greater; for latency, faster = lower. None when
        unplaceable (no value, or an empty field)."""
        if mine is None:
            return None
        vals = [v for v in values if v is not None]
        if not vals:
            return None
        if higher_is_faster:
            better = sum(1 for v in vals if v > mine)
        else:
            better = sum(1 for v in vals if v < mine)
        return better + 1

    def throughput_rank(self, permaslug: str) -> Optional[int]:
        r = self._by_perma.get(permaslug)
        if r is None:
            return None
        return self._rank([x.p50_throughput for x in self._all], r.p50_throughput, True)

    def latency_rank(self, permaslug: str) -> Optional[int]:
        r = self._by_perma.get(permaslug)
        if r is None:
            return None
        return self._rank([x.p50_latency for x in self._all], r.p50_latency, False)

    def field_size(self) -> int:
        """Count of models carrying a throughput value (the rankable field)."""
        return sum(1 for x in self._all if x.p50_throughput is not None)

    def standing(self, permaslug: str) -> Optional[SpeedStanding]:
        """A render-ready :class:`SpeedStanding` for a permaslug, or None if the
        model isn't in the performance field. Bundles both percentiles + ranks so
        the UI never re-derives fleet-relative math."""
        r = self._by_perma.get(permaslug)
        if r is None:
            return None
        return SpeedStanding(
            ranking=r,
            throughput_pct=self.throughput_percentile(permaslug),
            latency_pct=self.latency_percentile(permaslug),
            throughput_rank=self.throughput_rank(permaslug),
            latency_rank=self.latency_rank(permaslug),
            field_size=self.field_size(),
        )


def parse_performance(rows: list) -> SpeedBoard:
    """Build a :class:`SpeedBoard` from ``rankings/performance`` rows. Pure.
    Rows are keyed by permaslug (the ``id``/``slug`` fields are identical)."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append(SpeedRanking(
            permaslug=r.get("slug") or r.get("id") or "",
            name=r.get("name", ""),
            p50_throughput=as_float(r.get("p50_throughput"), None),
            p50_latency=as_float(r.get("p50_latency"), None),
            best_throughput_provider=r.get("best_throughput_provider"),
            best_throughput_price=as_float(r.get("best_throughput_price"), None),
            best_latency_provider=r.get("best_latency_provider"),
            best_latency_price=as_float(r.get("best_latency_price"), None),
            provider_count=int(r.get("provider_count") or 0),
            request_count=int(r.get("request_count") or 0),
        ))
    return SpeedBoard(out)


# ---------------------------------------------------------------------------
#  Week-over-week request momentum  (from /api/frontend/v1/rankings/models)
#  — feature #7 "THE TAPE": a per-model trending signal. Each row carries a
#  ``change`` FLOAT fraction (week-over-week request-volume delta): -1.0 = -100%
#  (dying), 0.50 = +50% riser, and explosive new entrants run to 247 / 727
#  (i.e. +24700%). 33/424 live rows lack a change → None (no glyph). Keyed by
#  the VERSIONED permaslug (``model_permaslug``), so the card resolves a pinned
#  model_id → permaslug via PermaslugResolver before looking it up here.
# ---------------------------------------------------------------------------
class TrendBoard:
    """Week-over-week request-momentum map: ``{permaslug: change_float|None}``.
    A row whose ``change`` is null lands as ``None`` so the card paints nothing
    (silent-degrade), distinct from a model that simply isn't ranked (a KeyError
    → :meth:`change` returns None too)."""

    def __init__(self, by_perma: dict):
        self._by_perma = dict(by_perma)

    def __len__(self):
        return len(self._by_perma)

    def change(self, permaslug):
        """The week-over-week change fraction for a permaslug, or None when the
        model isn't ranked OR its row carried no change value. Both miss-cases
        collapse to None so the caller has ONE silent-degrade path."""
        if not permaslug:
            return None
        return self._by_perma.get(permaslug)

    def as_map(self) -> dict:
        return dict(self._by_perma)


def parse_rankings_models(rows: list) -> TrendBoard:
    """Build a :class:`TrendBoard` from ``rankings/models`` rows. Pure.

    Rows are keyed by ``model_permaslug`` (NOT ``slug``/``id`` — those are null
    on this endpoint, re-verified live 2026-06-24). ``change`` is a float
    fraction; a missing/null/non-numeric change is retained as the key mapping
    to None (33/424 rows live), so the card can tell "ranked but no delta" from
    "absent from the board"."""
    by_perma = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        perma = r.get("model_permaslug") or r.get("slug") or r.get("id")
        if not perma:
            continue
        # Last writer wins on a permaslug collision (e.g. a :free variant row
        # sharing a base permaslug); the standard variant is listed first live.
        by_perma.setdefault(perma, as_float(r.get("change"), None))
    return TrendBoard(by_perma)


# ---------------------------------------------------------------------------
#  Public apps leaderboard  (from /api/frontend/v1/rankings/apps)
#  — feature #18 "THE CLIMB": the weekly public apps board (top-20), keyed by
#  rank. Each row's ``total_tokens`` is a JSON STRING (int()-coerced);
#  ``total_requests`` is an int. The user's 6.7M weekly tokens sits in the VALLEY
#  ~10,000x below even this board's floor (~73B), so the climb pins the user as a
#  lone marker below the lowest rung — NEVER an "out-tokened #N" claim (recon +
#  re-verify 2026-06-24 prove it false). NOAUTH, browser-UA session (a bare
#  python-requests UA is connection-reset by the frontend edge).
# ---------------------------------------------------------------------------
@dataclass
class AppRanking:
    """One public app's weekly traffic line (keyed by rank). ``total_tokens`` is
    coerced from the API's STRING to int; ``total_requests`` is already an int.
    ``title``/``slug``/``favicon_url`` drive the rung's label + favicon-dot."""
    rank: int = 0
    title: str = ""
    slug: str = ""
    favicon_url: Optional[str] = None
    total_tokens: int = 0          # coerced from a STRING ('7311066568924')
    total_requests: int = 0        # already an int on this endpoint


def parse_rankings_apps(data) -> list:
    """Build the weekly public-apps leaderboard from a ``rankings/apps`` payload.
    Pure.

    ``data`` is the ``{day, week, month}`` dict (each a top-20 list). We read the
    ``week`` list (#18 is a weekly board — comparable to the user's weekly token
    total). Each row's ``total_tokens`` arrives as a STRING and is coerced via
    int(); ``total_requests`` is an int; ``rank`` order is preserved as returned.
    Returns a list of :class:`AppRanking` (empty list on a missing/odd payload —
    never raises)."""
    week = []
    if isinstance(data, dict):
        week = data.get("week") or []
    out = []
    for r in week:
        if not isinstance(r, dict):
            continue
        app = r.get("app") or {}
        if not isinstance(app, dict):
            app = {}
        out.append(AppRanking(
            rank=as_int(r.get("rank")),
            title=app.get("title") or app.get("slug") or "",
            slug=app.get("slug") or "",
            favicon_url=app.get("favicon_url") or None,
            total_tokens=as_int(r.get("total_tokens")),
            total_requests=as_int(r.get("total_requests")),
        ))
    return out


# ---------------------------------------------------------------------------
#  Per-provider endpoint refs + uptime history
#  (from /api/frontend/v1/stats/endpoint and /stats/uptime-hourly)
# ---------------------------------------------------------------------------
@dataclass
class EndpointRef:
    """One provider's serving endpoint for a model — the bridge from a
    permaslug to the UUID that ``uptime-hourly`` needs."""
    id: str = ""                 # endpoint UUID (uptime-hourly?id=…)
    name: str = ""
    provider_name: str = ""
    provider_slug: str = ""      # e.g. 'amazon-bedrock/eu-west-1'
    is_deranked: bool = False
    is_disabled: bool = False
    status: int = 0


def parse_endpoint_refs(rows: list) -> list:
    """Parse ``stats/endpoint`` rows into :class:`EndpointRef`s. Pure."""
    out = []
    for e in rows or []:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if not eid:
            continue
        out.append(EndpointRef(
            id=eid,
            name=e.get("name", ""),
            provider_name=e.get("provider_name") or e.get("provider_display_name") or "",
            provider_slug=e.get("provider_slug", "") or "",
            is_deranked=bool(e.get("is_deranked")),
            is_disabled=bool(e.get("is_disabled")),
            status=int(e.get("status") or 0),
        ))
    return out


@dataclass
class UptimeHistory:
    """A provider endpoint's hourly uptime over the last ~73 hours, normalized
    to CHRONOLOGICAL order (oldest first → newest last) so a ribbon renders
    left=old, right=now. Each point is ``(date_str, value_or_None)``; ``None``
    marks an hour with no data."""
    points: list = field(default_factory=list)   # [(date_str, float|None), …]

    def __len__(self):
        return len(self.points)

    @property
    def values(self) -> list:
        return [v for _, v in self.points]

    @property
    def latest(self) -> Optional[float]:
        for _, v in reversed(self.points):
            if v is not None:
                return v
        return None

    @property
    def average(self) -> Optional[float]:
        vs = [v for v in self.values if v is not None]
        return sum(vs) / len(vs) if vs else None

    @property
    def worst(self) -> Optional[tuple]:
        """The (date_str, value) of the lowest-uptime hour, or None."""
        observed = [(d, v) for d, v in self.points if v is not None]
        return min(observed, key=lambda dv: dv[1]) if observed else None

    @property
    def outage_hours(self) -> int:
        """Count of observed hours below 99% uptime."""
        return sum(1 for v in self.values if v is not None and v < 99.0)


def parse_uptime_hourly(payload: dict) -> UptimeHistory:
    """Parse a ``stats/uptime-hourly`` payload. The API returns newest-first;
    we reverse to chronological. Pure."""
    data = (payload or {}).get("data") or {}
    hist = data.get("history") or []
    points = []
    for h in hist:
        if not isinstance(h, dict):
            continue
        v = h.get("uptime")
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        points.append((h.get("date", ""), v))
    points.reverse()   # newest-first → oldest-first (chronological)
    return UptimeHistory(points=points)


# ---------------------------------------------------------------------------
#  The client
# ---------------------------------------------------------------------------
class FrontendClient:
    """Synchronous no-auth client for the frontend API. Each fetch returns a
    parsed structure (or None / [] on failure) and never raises — callers run
    it on the worker thread and marshal results to the UI."""

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        # Direct assignment, NOT setdefault: a fresh requests.Session already
        # carries a 'python-requests/x' User-Agent, and the frontend website API
        # bot-blocks that UA (the connection is reset, not 403'd). We must
        # override it with a browser-ish UA or every frontend fetch fails.
        self.session.headers["User-Agent"] = USER_AGENT

    def _get_json(self, path: str, params: Optional[dict] = None, timeout: int = 20):
        url = BASE_URL + path
        r = self.session.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _fetch(self, path, parser, *, default=None, params=None, timeout=20,
               unwrap="data", unwrap_default=None, label=None):
        """Shared GET -> parse -> degrade skeleton for the wrappers below.
        Never raises (callers run on the worker thread): logs and returns
        ``default`` on any failure. ``unwrap`` pulls ``payload[unwrap]`` before
        parsing (None = pass the whole payload). ``label`` is the log tag."""
        try:
            payload = self._get_json(path, params=params, timeout=timeout)
            if unwrap is not None:
                payload = payload.get(unwrap, unwrap_default)
            return parser(payload)
        except Exception:
            log.warning("%s fetch failed", label or path, exc_info=True)
            return default

    def get_permaslug_resolver(self) -> Optional[PermaslugResolver]:
        return self._fetch("/api/frontend/v1/catalog/models", parse_catalog_permaslugs,
                           timeout=30, unwrap_default=[], label="catalog/models")

    def get_provider_trust(self) -> Optional[ProviderTrustBook]:
        return self._fetch("/api/frontend/all-providers", parse_all_providers,
                           unwrap_default=[], label="all-providers")

    def get_speed_board(self) -> Optional[SpeedBoard]:
        return self._fetch("/api/frontend/v1/rankings/performance", parse_performance,
                           unwrap_default=[], label="rankings/performance")

    def get_rankings_models(self) -> Optional[TrendBoard]:
        """THE TAPE (#7): the no-auth week-over-week request-momentum board
        (~191KB, keyed by permaslug). Returns None on failure so cards keep
        their last-good tape."""
        return self._fetch("/api/frontend/v1/rankings/models", parse_rankings_models,
                           unwrap_default=[], label="rankings/models")

    def get_rankings_apps(self) -> Optional[list]:
        """THE CLIMB (#18): the no-auth weekly public-apps leaderboard (top-20,
        keyed by rank). Returns a list of :class:`AppRanking` (parsed off the
        ``week`` list) or None on failure so the climb keeps its last-good
        ladder. MUST ride this client's browser-UA session (a bare requests.get
        is connection-reset by the frontend edge)."""
        return self._fetch("/api/frontend/v1/rankings/apps", parse_rankings_apps,
                           unwrap_default={}, label="rankings/apps")

    def get_endpoint_refs(self, permaslug: str, variant: str = "standard") -> list:
        return self._fetch("/api/frontend/v1/stats/endpoint", parse_endpoint_refs,
                           default=[], params={"permaslug": permaslug, "variant": variant},
                           unwrap_default=[], label=f"stats/endpoint({permaslug})")

    def get_uptime_hourly(self, endpoint_id: str) -> Optional[UptimeHistory]:
        return self._fetch("/api/frontend/v1/stats/uptime-hourly", parse_uptime_hourly,
                           params={"id": endpoint_id}, unwrap=None,
                           label=f"stats/uptime-hourly({endpoint_id})")
