"""
OpenRouter Pulse - API Client
Handles all communication with OpenRouter API endpoints.
"""
import logging
import re
import requests
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Optional
from PySide6.QtCore import QObject, Signal, Slot, QThread

from config import (
    API_KEY, API_KEY_ENDPOINT, MODELS_ENDPOINT,
    ANALYTICS_QUERY_ENDPOINT,
)
from num import as_float as _as_float, as_int as _as_int
from spend_model import (  # re-exported: extracted pure spend/insights logic
    _bucket_key, parse_analytics_query,
    SpendModel, SpendSpectrumData, SpendBoard, InsightsBoard,
    build_spend_spectrum, build_spend_board,
    AutopsyRow, AutopsyReport, build_autopsy, AUTOPSY_MAX_ROWS,
    Receipt, build_receipts, RECEIPT_MIN_STAMP_REQUESTS, RECEIPT_MIN_HISTORY_DAYS,
    SavingsModel, Savings, build_savings,
    GhostPair, GhostEntry, GhostDiff, parse_ghost_diff, build_ghost_diff,
    Budget, build_budget, budget_geometry, HOURGLASS_BULB_H,
)

log = logging.getLogger("pulse.api")

BASE_URL = "https://openrouter.ai"
CREDITS_ENDPOINT = f"{BASE_URL}/api/v1/credits"
BENCHMARKS_ENDPOINT = f"{BASE_URL}/api/v1/benchmarks"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://github.com/k4rg1l/pulse",
    "X-OpenRouter-Title": "Pulse",
}


@dataclass
class KeyInfo:
    label: str = ""
    limit: Optional[float] = None
    limit_remaining: Optional[float] = None
    limit_reset: Optional[str] = None
    usage: float = 0.0
    usage_daily: float = 0.0
    usage_weekly: float = 0.0
    usage_monthly: float = 0.0
    is_free_tier: bool = False
    total_credits: float = 0.0
    total_usage: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def remaining(self):
        if self.limit_remaining is not None:
            return self.limit_remaining
        if self.total_credits > 0:
            return max(0, self.total_credits - self.total_usage)
        return None

    @property
    def credit_percent(self):
        rem = self.remaining
        if rem is not None and self.total_credits > 0:
            return rem / self.total_credits
        if self.limit is not None and self.limit > 0 and self.limit_remaining is not None:
            return self.limit_remaining / self.limit
        return 1.0

    @property
    def burn_rate_daily(self):
        return self.usage_daily if self.usage_daily > 0 else 0.0

    @property
    def burn_rate_hourly(self):
        return self.burn_rate_daily / 24.0 if self.burn_rate_daily > 0 else 0.0

    @property
    def days_remaining(self):
        rem = self.remaining
        if rem is not None and self.burn_rate_daily > 0:
            return rem / self.burn_rate_daily
        return float('inf')


@dataclass
class ModelInfo:
    id: str = ""
    name: str = ""
    pricing_prompt: float = 0.0
    pricing_completion: float = 0.0
    context_length: int = 0

    @property
    def price_per_mtok_prompt(self):
        return self.pricing_prompt * 1_000_000

    @property
    def price_per_mtok_completion(self):
        return self.pricing_completion * 1_000_000


# The extended pricing keys F1 retains beyond prompt/completion. Every one is a
# STRING $/unit upstream EXCEPT `discount` (already numeric). The public route
# omits zero-value keys, so we only float() the keys that are actually present —
# an absent key stays absent (never coerced to 0.0). See EndpointInfo.fee().
_PRICING_EXTRA_KEYS = (
    "input_cache_read", "input_cache_write", "web_search", "image", "audio",
    "input_audio_cache", "internal_reasoning", "request", "discount",
)


def _parse_pricing_extra(pricing: dict) -> dict:
    """Retain the FULL pricing object (minus prompt/completion, kept as explicit
    fields) as a sparse {key: float} dict. CRITICAL: a key ABSENT from the
    upstream payload stays ABSENT here — it is NOT defaulted to 0.0 — because a
    present-with-value>0 key is the only signal that a hidden fee applies (#6)."""
    out: dict = {}
    if not isinstance(pricing, dict):
        return out
    for k in _PRICING_EXTRA_KEYS:
        if k not in pricing:          # sparse omission → leave absent (not 0.0)
            continue
        v = pricing[k]
        try:
            out[k] = float(v)         # discount is numeric; the rest are strings
        except (ValueError, TypeError):
            continue                  # unparseable → treat as absent
    return out


def _ep_percentile(ep: dict, field: str, key: str) -> Optional[float]:
    """Pull one percentile (e.g. p50) from a {p50,p75,p90,p99} dict field, or
    accept a bare scalar, else None."""
    v = ep.get(field)
    if isinstance(v, dict):
        n = v.get(key)
        return float(n) if n is not None else None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def parse_model_endpoints(model_id: str, data: dict) -> ModelEndpoints:
    """Build ModelEndpoints from the public /api/v1/models/{slug}/endpoints
    `data` object. Module-level (mirrors parse_benchmarks) so the widened F1
    pricing parse is unit-testable against a captured payload without network.
    prompt/completion are float()d here as before; the rest of the pricing
    object is carried sparse via _parse_pricing_extra (absent stays absent)."""
    endpoints = []
    for ep in (data or {}).get("endpoints", []):
        pricing = ep.get("pricing", {})
        pp = _as_float(pricing.get("prompt", "0"))
        cp = _as_float(pricing.get("completion", "0"))
        endpoints.append(EndpointInfo(
            provider_name=ep.get("provider_name", ""),
            tag=ep.get("tag", ""),
            quantization=ep.get("quantization", ""),
            context_length=ep.get("context_length", 0),
            pricing_prompt=pp,
            pricing_completion=cp,
            uptime_last_30m=ep.get("uptime_last_30m"),
            uptime_last_5m=ep.get("uptime_last_5m"),
            uptime_last_1d=ep.get("uptime_last_1d"),
            latency_p50=_ep_percentile(ep, "latency_last_30m", "p50"),
            latency_p90=_ep_percentile(ep, "latency_last_30m", "p90"),
            throughput_p50=_ep_percentile(ep, "throughput_last_30m", "p50"),
            status=ep.get("status", 0),
            supports_implicit_caching=ep.get("supports_implicit_caching", False),
            pricing_extra=_parse_pricing_extra(pricing),
        ))
    return ModelEndpoints(
        model_id=model_id,
        model_name=(data or {}).get("name", model_id),
        endpoints=endpoints,
    )


@dataclass
class EndpointInfo:
    """One provider's offering of a model: latency, uptime, price.

    Latency in the API is a percentile dict {p50, p75, p90, p99} (or null).
    We extract p50 for the primary metric and keep p90 around for tooltips.
    """
    provider_name: str = ""
    tag: str = ""                       # e.g. "amazon-bedrock/eu-west-1"
    quantization: str = ""
    context_length: int = 0
    pricing_prompt: float = 0.0         # $/token
    pricing_completion: float = 0.0
    uptime_last_30m: Optional[float] = None  # 0..100
    uptime_last_5m: Optional[float] = None
    uptime_last_1d: Optional[float] = None
    latency_p50: Optional[float] = None  # ms
    latency_p90: Optional[float] = None
    throughput_p50: Optional[float] = None  # tokens/sec
    status: int = 0
    supports_implicit_caching: bool = False
    # F1 — the FULL pricing object beyond prompt/completion (shared foundation,
    # primary consumer #6 THE WATERLINE). The public /endpoints route OMITS
    # zero-value keys (sparse), so an ABSENT key MUST stay None — never coerced
    # to 0.0 — because "key present with value > 0" is the signal that a hidden
    # fee actually applies. Every $/token field is a STRING upstream (float()d
    # at parse, mirroring prompt/completion); `discount` is already numeric.
    # Units: cache/image/audio/internal_reasoning are $/token; web_search is
    # $/call; request is $/request; discount is a fraction (0..1).
    pricing_extra: dict = field(default_factory=dict)

    # backwards-compat alias for older readers
    @property
    def latency_last_30m(self):
        return self.latency_p50

    @property
    def throughput_last_30m(self):
        return self.throughput_p50

    @property
    def price_per_mtok_prompt(self):
        return self.pricing_prompt * 1_000_000

    @property
    def price_per_mtok_completion(self):
        return self.pricing_completion * 1_000_000

    def fee(self, key: str) -> Optional[float]:
        """A hidden-fee value (e.g. 'input_cache_read', 'web_search') or None
        if the upstream payload omitted that key. None == "this fee does not
        apply" (sparse route); a 0.0 would be a real, explicit zero fee."""
        return self.pricing_extra.get(key)

    def has_fee(self, key: str) -> bool:
        """True iff the fee key was present AND its value is > 0 — the exact
        signal #6 keys off ("present with value > 0 == applies")."""
        v = self.pricing_extra.get(key)
        return v is not None and v > 0

    @property
    def uptime(self):
        """Best uptime signal available: prefer 30m, then 1d, then 5m."""
        for v in (self.uptime_last_30m, self.uptime_last_1d, self.uptime_last_5m):
            if v is not None:
                return v
        return None


@dataclass
class ModelEndpoints:
    """The full endpoint list for one model."""
    model_id: str
    model_name: str = ""
    endpoints: list = field(default_factory=list)

    def best_provider(self) -> Optional[EndpointInfo]:
        """Lowest p50 latency among providers with uptime >= 99 percent.
        Tie-breaker: cheaper prompt price. None if no metrics available."""
        candidates = [
            e for e in self.endpoints
            if e.latency_p50 is not None
            and (e.uptime is None or e.uptime >= 99.0)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda e: (e.latency_p50, e.pricing_prompt))
        return candidates[0]


# ---------------------------------------------------------------------------
#  #5 THE THRESHOLD — the "Cheapest Door"
#
#  Pure local math over the EndpointInfo list the card already holds (NO new
#  fetch). The CURRENT provider is the card's _best (lowest p50 latency among
#  uptime>=99, cheaper-prompt tie-break). The cheaper DESTINATION is the
#  endpoint with the minimum prompt price (>0). The band paints a perspective
#  door swung open toward that destination, the saving % engraved on the lintel.
#
#  GREEN-DOOR RULE (the rare cheaper-AND-faster case — one deterministic,
#  testable rule, decision A): cheaper.pricing_prompt < best.pricing_prompt AND
#  cheaper.throughput_p50 > best.throughput_p50 (STRICTLY higher throughput).
#  Not widened to "or lower latency".
# ---------------------------------------------------------------------------

# Brass-amber lane (normal) + emerald (green-door). #5 owns these two; kept
# region-separate from Speed cyan / Arena tiers / Ledger / Pulse green.
DOOR_AMBER = "#e0a13a"
DOOR_EMERALD = "#34d27e"


@dataclass
class DoorResolution:
    """The resolved 'cheapest door' for one model (or the absence of one).

    save_pct      — round(100 * (best - cheaper)/best); always >= 1 when present.
    cheaper_name  — destination provider display name.
    from_*/to_*   — the FROM (current/best) and THROUGH (cheaper) metrics for the
                    dossier, so it never overstates.
    green         — cheaper is ALSO strictly faster (higher throughput_p50).
    """
    save_pct: int = 0
    cheaper_name: str = ""
    from_name: str = ""
    from_prompt: float = 0.0          # $/token
    from_latency: Optional[float] = None
    from_throughput: Optional[float] = None
    to_prompt: float = 0.0
    to_latency: Optional[float] = None
    to_throughput: Optional[float] = None
    green: bool = False

    @property
    def accent(self) -> str:
        return DOOR_EMERALD if self.green else DOOR_AMBER

    @property
    def from_mtok(self) -> float:
        return self.from_prompt * 1_000_000

    @property
    def to_mtok(self) -> float:
        return self.to_prompt * 1_000_000

    @property
    def latency_delta_pct(self) -> Optional[int]:
        """How much SLOWER (+) or faster (-) first-token the cheaper door is, vs
        the current provider, as a signed % — the honesty-line input. None if
        either latency is missing."""
        if self.from_latency is None or self.to_latency is None or self.from_latency <= 0:
            return None
        return round(100 * (self.to_latency - self.from_latency) / self.from_latency)


def resolve_door(endpoints, best) -> Optional[DoorResolution]:
    """Pure resolution of THE THRESHOLD. Returns a DoorResolution, or None for
    every no-op case (decision C — the band then paints nothing):
      * best is None (no current provider),
      * no priced (prompt>0) endpoint exists,
      * the cheapest priced endpoint IS best (already on the cheapest door),
      * best.pricing_prompt == 0 (free model → divide-by-zero guard),
      * the saving rounds to 0%.
    No fake data is ever invented."""
    if best is None or not endpoints:
        return None
    if not best.pricing_prompt or best.pricing_prompt <= 0:
        return None                                   # free / unpriced → no door
    priced = [e for e in endpoints if e.pricing_prompt and e.pricing_prompt > 0]
    if not priced:
        return None
    cheapest = min(priced, key=lambda e: e.pricing_prompt)
    if cheapest is best or cheapest.pricing_prompt >= best.pricing_prompt:
        return None                                   # best already cheapest
    save_pct = round(100 * (best.pricing_prompt - cheapest.pricing_prompt)
                     / best.pricing_prompt)
    if save_pct <= 0:                                 # rounds to nothing → no door
        return None
    # GREEN-DOOR: cheaper AND strictly-higher throughput (decision A). Both
    # throughputs must be known to claim it.
    green = (
        cheapest.pricing_prompt < best.pricing_prompt
        and best.throughput_p50 is not None
        and cheapest.throughput_p50 is not None
        and cheapest.throughput_p50 > best.throughput_p50
    )
    return DoorResolution(
        save_pct=save_pct,
        cheaper_name=cheapest.provider_name or cheapest.tag or "cheaper provider",
        from_name=best.provider_name or best.tag or "current provider",
        from_prompt=best.pricing_prompt,
        from_latency=best.latency_p50,
        from_throughput=best.throughput_p50,
        to_prompt=cheapest.pricing_prompt,
        to_latency=cheapest.latency_p50,
        to_throughput=cheapest.throughput_p50,
        green=green,
    )


# ---------------------------------------------------------------------------
#  #6 THE WATERLINE — the hidden-cost iceberg (pure layer)
#
#  The listed prompt/completion price is the visible tip; the submerged mass is
#  every OTHER fee class the public route carries but the row can't show. We
#  COLLAPSE the sparse pricing_extra keys into FOUR fee CLASSES (decision A) so
#  the depth caps at a clean denominator, and count a class ONLY when one of its
#  member fees is present with value > 0 (decision B — key-presence alone, e.g.
#  a zero-padded key or discount==0, must NOT count).
#
#    cache     = input_cache_read OR input_cache_write   (> 0)
#    search    = web_search                              (> 0)   ($/call)
#    reasoning = internal_reasoning                      (> 0)
#    media     = image OR audio OR input_audio_cache     (> 0)
#
#  hidden_count = |classes present|;  depth = hidden_count / HIDDEN_MAX. The
#  denominator is HIDDEN_MAX=5 (NOT 4) on purpose: max real depth is 4/5, an
#  intentional headroom so a fully-loaded row never reads as "100% submerged".
#  A clean row (only prompt+completion) yields an EMPTY set and depth 0 — the
#  card then draws nothing (silent honest degrade, decision D). `discount` is
#  deliberately NOT a fee class (it's a price CUT, not a hidden charge).
# ---------------------------------------------------------------------------

# Marine "deep water" lane (steel surface / abyss submerged / pale-aqua edge /
# hollow buoy). Deliberately darker + greener than Speed's cyan #00d2ff, not
# the Pulse green, never red. #6 owns these.
WATERLINE_SURFACE = "#2f7d8a"   # the calm sea the price floats on
WATERLINE_ABYSS = "#0e4d5c"     # submerged mass + ticks + buoy ring
WATERLINE_EDGE = "#7fd6e0"      # 1px sea-level line

HIDDEN_MAX = 5                  # depth denominator (decision A — /5 headroom)

# class -> the member fee keys; a class applies iff ANY member has value > 0.
_FEE_CLASS_MEMBERS = {
    "cache": ("input_cache_read", "input_cache_write"),
    "search": ("web_search",),
    "reasoning": ("internal_reasoning",),
    "media": ("image", "audio", "input_audio_cache"),
}
# A stable display order for the dossier / ticks (left → right).
FEE_CLASS_ORDER = ("cache", "search", "reasoning", "media")


def hidden_fee_classes(ep) -> frozenset:
    """The set of hidden-fee CLASSES an EndpointInfo carries (decision A/B).

    Pure over ep.has_fee() (which is True only for a present key with value>0),
    so an absent key, an explicit-zero fee, and discount all correctly DON'T
    count. Returns a frozenset of class names ⊆ {'cache','search','reasoning',
    'media'} — empty for a clean prompt+completion-only row."""
    out = set()
    for cls, members in _FEE_CLASS_MEMBERS.items():
        if any(ep.has_fee(k) for k in members):
            out.add(cls)
    return frozenset(out)


def hidden_fee_depth(classes) -> float:
    """The submerged fraction for a set of fee classes: |classes| / HIDDEN_MAX.
    0.0 for a clean row; the live max is 4/5=0.8 (decision A headroom)."""
    return len(classes) / HIDDEN_MAX


# ---------------------------------------------------------------------------
#  The Arena — model competitive standings from /api/v1/benchmarks
#
#  DesignArena rates models by ELO + head-to-head win-rate across creative-
#  coding categories (svg, website, gamedev, asciiart, …) and tracks each
#  model's lifetime tournament podium finishes. We turn that into a ranked-
#  ladder "crest" per model: a global rank we compute ourselves (the API
#  gives ELO, not rank), a tier derived from the model's best rank-percentile,
#  and its lifetime medal haul. Artificial Analysis adds intelligence/coding/
#  agentic indices as the model's "base stats".
# ---------------------------------------------------------------------------

# Tier ladder, best (lowest) rank-percentile first. (name, hex). CHAMPION is
# the special case of an outright #1 finish in any category.
ARENA_TIERS = [
    (0.03, "GRANDMASTER", "#ff7ad9"),
    (0.08, "MASTER",      "#b98cff"),
    (0.15, "DIAMOND",     "#6ad0ff"),
    (0.30, "PLATINUM",    "#39d0b4"),
    (0.50, "GOLD",        "#e8b54a"),
    (0.75, "SILVER",      "#b9c2d6"),
    (2.00, "BRONZE",      "#c08552"),
]
ARENA_CHAMPION = ("CHAMPION", "#ffd23f")
# Tiers that earn the animated shimmer on the crest (the "legendary" feel).
ARENA_ELITE = {"CHAMPION", "GRANDMASTER", "MASTER", "DIAMOND"}


def _tier_for(rank: int, field_size: int):
    """(tier_name, hex_color) from a global rank within a field."""
    if rank <= 1:
        return ARENA_CHAMPION
    pct = rank / max(1, field_size)
    for thr, name, color in ARENA_TIERS:
        if pct <= thr:
            return name, color
    return ARENA_TIERS[-1][1], ARENA_TIERS[-1][2]


def _norm_model_name(name: str) -> str:
    """Normalize a model name/slug to a match key: drop an 'Author: ' prefix,
    lowercase, and reduce to space-separated alphanumerics. So 'Anthropic:
    Claude Opus 4.8', 'Claude Opus 4.8', and the slug tail 'claude-opus-4.8'
    all collapse to 'claude opus 4 8'."""
    s = name.split(": ", 1)[-1].lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


@dataclass
class CategoryStanding:
    """One model's standing in one DesignArena category."""
    category: str
    elo: int
    win_rate: float          # 0..100
    rank: int                # global rank we computed (1 = best)
    field_size: int          # models rated in this category

    @property
    def percentile(self) -> float:
        return self.rank / max(1, self.field_size)

    @property
    def tier(self):
        return _tier_for(self.rank, self.field_size)


@dataclass
class BenchmarkEntry:
    """A model's full Arena dossier."""
    display_name: str
    standings: list = field(default_factory=list)   # best-percentile first
    intelligence: Optional[float] = None             # Artificial Analysis indices
    coding: Optional[float] = None
    agentic: Optional[float] = None
    golds: int = 0                                    # lifetime tournament podiums
    silvers: int = 0
    bronzes: int = 0
    battles: int = 0

    @property
    def signature(self) -> Optional[CategoryStanding]:
        """The model's best showing — drives the crest."""
        return self.standings[0] if self.standings else None

    @property
    def peak_elo(self) -> Optional[int]:
        return max((s.elo for s in self.standings), default=None)

    @property
    def tier(self):
        s = self.signature
        return s.tier if s else ("UNRANKED", "#64648c")

    @property
    def is_elite(self) -> bool:
        return self.tier[0] in ARENA_ELITE


class BenchmarkBoard:
    """Lookup of model -> BenchmarkEntry, matched by a normalized name key
    derived from either the pinned model id or its display name."""

    def __init__(self, entries: dict):
        self._entries = entries  # norm_key -> BenchmarkEntry

    def __len__(self):
        return len(self._entries)

    def lookup(self, model_id: str, display_name: Optional[str] = None) -> Optional[BenchmarkEntry]:
        keys = []
        if display_name:
            keys.append(_norm_model_name(display_name))
        keys.append(_norm_model_name(model_id.split("/")[-1]))
        for k in keys:
            if k in self._entries:
                return self._entries[k]
        return None


def parse_benchmarks(da_rows: list, aa_rows: list) -> BenchmarkBoard:
    """Build a BenchmarkBoard from DesignArena + Artificial Analysis rows.
    Pure (no I/O) so it can be unit-tested against captured samples. The API
    returns ELO but not rank, so we compute global ranks per category here."""
    # Rank every model within each category by ELO (desc).
    by_cat = defaultdict(list)
    for r in da_rows:
        by_cat[r.get("category", "")].append(r)
    ranks = {}  # (display_name, category) -> (rank, field_size)
    for cat, rows in by_cat.items():
        ordered = sorted(rows, key=lambda x: -(x.get("elo") or 0))
        n = len(ordered)
        for i, r in enumerate(ordered, 1):
            ranks[(r.get("display_name", ""), cat)] = (i, n)

    entries = {}
    for r in da_rows:
        name = r.get("display_name", "")
        if not name:
            continue
        key = _norm_model_name(name)
        e = entries.get(key)
        if e is None:
            ts = r.get("tournament_stats") or {}
            e = BenchmarkEntry(
                display_name=name,
                golds=int(ts.get("first_place") or 0),
                silvers=int(ts.get("second_place") or 0),
                bronzes=int(ts.get("third_place") or 0),
                battles=int(ts.get("total") or 0),
            )
            entries[key] = e
        rank, field_size = ranks.get((name, r.get("category", "")), (0, 0))
        if rank:
            e.standings.append(CategoryStanding(
                category=r.get("category", ""),
                elo=int(r.get("elo") or 0),
                win_rate=float(r.get("win_rate") or 0.0),
                rank=rank,
                field_size=field_size,
            ))

    for e in entries.values():
        e.standings.sort(key=lambda s: (s.percentile, s.rank))

    # Attach Artificial Analysis base stats (its display names carry parenthetical
    # variant suffixes — match on the part before " (").
    aa_by = {}
    for r in aa_rows:
        base = (r.get("display_name", "") or "").split(" (")[0]
        if base:
            aa_by.setdefault(_norm_model_name(base), r)
    for key, e in entries.items():
        a = aa_by.get(key)
        if a:
            e.intelligence = a.get("intelligence_index")
            e.coding = a.get("coding_index")
            e.agentic = a.get("agentic_index")

    return BenchmarkBoard(entries)


class AnalyticsClient:
    """Read-only client for the OpenRouter analytics API (ground-truth spend).

    Mirrors APIClient's shape (a requests.Session + last_error) but carries a
    SEPARATE session keyed on the MANAGEMENT key. When no management key is
    present it is `unlocked=False` and every call returns None WITHOUT touching
    the network (the LOCKED sentinel). It NEVER raises to the worker — any
    failure sets last_error and returns None so the zone keeps last-good / shows
    locked. The management key is used read-only and is NEVER logged/printed.

    query() caches parsed envelopes by (frozenset(metrics), tuple(dims),
    granularity, start, end) with the response's cachedAt as the TTL anchor, so
    a same-key re-poll inside the window is free (the 15-min poll re-hits Query
    A for free within TTL).
    """

    # TTL a touch under the 15-min poll so a re-poll of an unchanged key reuses
    # the cached envelope instead of re-hitting the rate-limited endpoint.
    CACHE_TTL_SECONDS = 14 * 60
    # Hard cap on cached envelopes. The cache key embeds a now()-based date
    # window, so each poll mints new keys; the cap evicts old poll cycles'
    # entries so the dict can't grow without bound over a long uptime.
    CACHE_MAX = 64

    def __init__(self):
        import config
        self.mgmt_key = config.MANAGEMENT_KEY
        self.unlocked = bool(self.mgmt_key)
        self.session = requests.Session()
        # A SEPARATE session/headers from APIClient — do NOT reuse self.session
        # elsewhere. Authorization carries the mgmt key.
        self.session.headers.update({
            "Authorization": f"Bearer {self.mgmt_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": HEADERS["HTTP-Referer"],
            "X-OpenRouter-Title": "Pulse",
        })
        self._cache: dict = {}
        self.last_error: Optional[str] = None

    def query(self, metrics: list, dimensions: list, granularity: str,
              start: str, end: str) -> Optional[dict]:
        """Cached analytics POST. Returns the PARSED envelope dict
        ({"rows", "metadata", "cachedAt"}) or None (locked / failure). Never
        raises."""
        if not self.unlocked:
            return None  # LOCKED sentinel — no network
        key = (frozenset(metrics), tuple(dimensions), granularity, start, end)
        hit = self._cache.get(key)
        if hit is not None:
            cached_at, parsed = hit
            if (time.time() - cached_at) < self.CACHE_TTL_SECONDS:
                return parsed
        try:
            body = {
                "metrics": metrics,
                "dimensions": dimensions,
                "granularity": granularity,
                # date_range:{start,end} — confirmed against /analytics/meta + a
                # live 200 query.
                "date_range": {"start": start, "end": end},
            }
            resp = self.session.post(ANALYTICS_QUERY_ENDPOINT, json=body, timeout=20)
            resp.raise_for_status()
            j = resp.json()
            # Envelope: {"data": {"data":[rows], "metadata":{...}, "cachedAt":ms}}
            inner = j.get("data", j) if isinstance(j, dict) else {}
            parsed = parse_analytics_query(inner)
            self._cache[key] = (time.time(), parsed)
            # Dicts keep insertion order -> pop the oldest once past the cap.
            while len(self._cache) > self.CACHE_MAX:
                self._cache.pop(next(iter(self._cache)))
            self.last_error = None
            return parsed
        except requests.exceptions.HTTPError as e:
            resp = e.response
            code = resp.status_code if resp is not None else None
            self.last_error = f"HTTP {code}" if code else "HTTP error"
            # Log status only — never the key, never the (private) body content.
            log.warning("analytics query: HTTP error status=%s", code)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("analytics query failed: %s", type(e).__name__)
        return None

    def get_credits(self) -> Optional[dict]:
        """GET /api/v1/credits via the mgmt-key session (the recon confirmed the
        mgmt key works on /credits). Returns {"total_credits","total_usage"} as
        floats, or None on failure / when locked. The ONLY live spend-cap signal
        (the #14 credits fallback). Never raises; never logs the key."""
        if not self.unlocked:
            return None
        try:
            resp = self.session.get(CREDITS_ENDPOINT, timeout=15)
            resp.raise_for_status()
            j = resp.json()
            d = j.get("data", {}) if isinstance(j, dict) else {}
            return {
                "total_credits": _as_float(d.get("total_credits")),
                "total_usage": _as_float(d.get("total_usage")),
            }
        except Exception as e:
            self.last_error = f"{type(e).__name__}"
            log.warning("credits fetch failed: %s", type(e).__name__)
            return None

    def _build_budget(self, end, end_iso: str) -> Optional[Budget]:
        """#14 QUERY D — the period-to-date day query (total_usage, dims=[],
        granularity=day, last `period_days`) + the budget denominator resolution
        (decision A, never invent one):
          (1) settings.weekly_budget > 0 -> source="weekly"; ELSE
          (2) settings.show_credit_burndown -> the credits fallback
              (budget=total_credits, burned=total_usage); ELSE
          (3) the honest "Set a budget" no-budget state.
        Cached by its own key (the 15-min poll re-hits free within TTL). Never
        raises — returns a Budget('none') sentinel on any failure so the widget
        still paints (never blanks)."""
        import datetime
        period_days = 7
        try:
            from settings import Settings
            s = Settings.load()
            weekly = float(getattr(s, "weekly_budget", 0.0) or 0.0)
            credit_opt_in = bool(getattr(s, "show_credit_burndown", False))
        except Exception:
            weekly, credit_opt_in = 0.0, False

        # QUERY D: total_usage only, NO dimensions, day granularity (decision D).
        d_start = (end - datetime.timedelta(days=period_days)).isoformat()
        env_d = self.query(["total_usage"], [], "day", d_start, end_iso)
        rows_d = (env_d.get("rows") or []) if env_d else []

        if weekly > 0:
            return build_budget(rows_d, weekly, d_start, end_iso,
                                source="weekly", period_days=period_days)
        if credit_opt_in:
            credits = self.get_credits()
            if credits is not None and credits["total_credits"] > 0:
                return build_budget(
                    rows_d, credits["total_credits"], d_start, end_iso,
                    source="credits", period_days=period_days,
                    credits_spent=credits["total_usage"])
            # opt-in on but credits unavailable -> degrade to "Set a budget".
        return build_budget(rows_d, 0.0, d_start, end_iso, source="none",
                            period_days=period_days)

    def get_spend_board(self) -> Optional[SpendBoard]:
        """The ONE batched call the Spend zone shares. Issues QUERY A now: a
        day-granularity dims=[model] query over the last 7d with the UNION
        metric list, so #10/#12 can ride this SAME cached envelope later with no
        new query. Returns a SpendBoard (with .spectrum populated) or None when
        locked / on failure. Never raises."""
        if not self.unlocked:
            return None
        try:
            import datetime
            end = datetime.datetime.now(datetime.timezone.utc)
            start = end - datetime.timedelta(days=7)
            # <=2-day ranges would use 'hour'; the standing zone is 7d -> 'day'.
            span_days = (end - start).total_seconds() / 86400.0
            granularity = "hour" if span_days <= 2 else "day"
            union_metrics = [
                "total_usage", "request_count", "tokens_total", "tokens_prompt",
                "tokens_completion", "reasoning_tokens", "cached_tokens",
                "usage_cache", "cache_hit_rate",
            ]
            start_iso = start.isoformat()
            end_iso = end.isoformat()
            parsed = self.query(union_metrics, ["model"], granularity,
                                start_iso, end_iso)
            if parsed is None:
                return None
            meta = parsed.get("metadata") or {}

            # #13 THE SÉANCE — ONE wide-range week-granularity dims=[model,provider]
            # query (cached by its key; the 15-min poll re-hits free within TTL).
            # The OpenRouter `week` query IGNORES date_range (re-verified
            # 2026-06-24): both the old Window A and Window B POSTs returned
            # BYTE-IDENTICAL data — 2 redundant requests against a rate-limited
            # endpoint. A single ~21-day range ensures all returned
            # created_at__week buckets are captured. parse_ghost_diff itself
            # pools rows and keys off DISTINCT bucket dates (decision B), so it
            # is handed the full envelope as env_a and None for env_b. Bucket
            # splitting (latest vs 2nd-latest) is performed inside
            # parse_ghost_diff — no row is double-counted.
            ghosts = None
            try:
                ghost_metrics = ["request_count", "total_usage"]
                # Wide range: ~3 weeks back so multiple week buckets are captured
                # if/when the API ever returns them; today it returns one.
                wide_start = (end - datetime.timedelta(days=21)).isoformat()
                env_a = self.query(ghost_metrics, ["model", "provider"],
                                   "week", wide_start, end_iso)
                # query() returns None only when locked/failure; we're unlocked
                # here, so None means a transient failure -> treat as empty rows
                # (parse_ghost_diff is None-safe and falls into young_history,
                # which is the calm, honest degrade — never a fake apparition).
                ghosts = parse_ghost_diff(
                    env_a, None,
                    range_a="this week", range_b="prior week",
                )
            except Exception:
                log.exception("ghost diff fetch crashed")
                ghosts = None

            # #14 THE HOURGLASS — QUERY D (period-to-date) + the budget
            # denominator (decision A; NEVER invent one). Routed through query()
            # so it caches by its own key. None-safe: a failure -> a Budget
            # sentinel so the widget paints, never blanks.
            budget = None
            try:
                budget = self._build_budget(end, end_iso)
            except Exception:
                log.exception("budget fetch crashed")
                budget = None

            board = build_spend_board(
                parsed.get("rows") or [],
                granularity=granularity,
                start=start_iso,
                end=end_iso,
                range_label="Last 7 Days",
                truncated=bool(meta.get("truncated")),
                ghosts=ghosts,
            )
            if budget is not None:
                board = replace(board, budget=budget)
            # #10 receipts INFO line (no secret — counts + top $/call only).
            try:
                rcs = board.receipts
                top_pc = max((r.per_call for r in rcs), default=0.0)
                n_stamped = sum(1 for r in rcs if r.has_stamp)
                log.info("receipts: %d models, top $/call=$%.4f, stamped=%d",
                         len(rcs), top_pc, n_stamped)
            except Exception:
                pass
            # #12 savings INFO line (no secret — rebate $, hit %, reasoning count).
            try:
                sv = board.savings
                if sv is not None:
                    log.info("savings: rebate=$%.2f, hit=%.1f%%, rsn=%d tok",
                             sv.total_rebate, sv.hit_rate_pct,
                             sv.reasoning_total)
            except Exception:
                pass
            # #13 ghosts INFO line (no secret — roster counts + young flag only).
            try:
                g = board.ghosts
                if g is not None:
                    log.info("ghosts: living=%d appeared=%d vanished=%d young=%s",
                             len(g.living), len(g.appeared), len(g.vanished),
                             bool(g.young_history))
            except Exception:
                pass
            # #14 budget INFO line (no secret — source + $ magnitudes + flags).
            try:
                b = board.budget
                if b is not None and b.has_budget:
                    log.info("budget: source=%s spent=$%.2f budget=$%.2f pct=%d%% "
                             "over_pace=%s proj=$%.2f", b.source, b.spent,
                             b.budget, b.pct_burned, b.over_pace, b.projection)
                else:
                    log.info("budget: no budget configured (source=%s)",
                             b.source if b is not None else "none")
            except Exception:
                pass
            return board
        except Exception:
            log.exception("get_spend_board crashed")
            return None

    @staticmethod
    def _parse_iso(s: str):
        """Best-effort parse of a lasso bucket label / ISO into an aware UTC
        datetime. Accepts '2026-06-22', '2026-06-22T12:00:00+00:00', and the
        live created_at__hour space form '2026-06-22 12:00:00'. Returns None if
        unparseable (the caller degrades gracefully)."""
        import datetime
        if not s:
            return None
        txt = str(s).strip().replace(" ", "T", 1)
        try:
            dt = datetime.datetime.fromisoformat(txt)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt

    @staticmethod
    def _is_bare_date(s: str) -> bool:
        """True for a 'YYYY-MM-DD' day-bucket label (no time component). The
        standing 7d Spectrum is DAY granularity, so a lasso/tap there emits bare
        dates; an hour-granularity selection carries a T/space + HH."""
        s = str(s or "").strip()
        return len(s) == 10 and "T" not in s and " " not in s

    def get_autopsy(self, t0_iso: str, t1_iso: str) -> Optional["AutopsyReport"]:
        """#11 — the interaction-fired drill-down. Clamps the lassoed window to a
        whole-hour query range and runs ONE hourly dims=[model,provider] query
        (cached by its key — F3 contract), then builds the AutopsyReport. The
        standing Spectrum is DAY-granularity, so a lasso/tap there yields bare
        dates -> the window is the full UTC day(s) and the label reads as the
        date(s); an hour-grained selection clamps to [floor(t0)h, ceil(t1)h] and
        labels 'HH:00–HH:00'. Returns None when locked / on failure (the worker
        emits that through so the dossier degrades, never crashes). Never raises;
        never logs the key. usage_cache NEGATIVE -> a GREEN offset (in
        build_autopsy)."""
        if not self.unlocked:
            return None
        import datetime
        try:
            d0 = self._parse_iso(t0_iso)
            d1 = self._parse_iso(t1_iso)
            if d0 is None:
                return None
            if d1 is None:
                d1 = d0
            if d1 < d0:
                d0, d1 = d1, d0
            day_grain = self._is_bare_date(t0_iso) and self._is_bare_date(t1_iso)
            if day_grain:
                # A DAY selection -> span the full UTC day(s); the query is still
                # hour-grained (the autopsy drills the hours WITHIN the day).
                start = d0.replace(hour=0, minute=0, second=0, microsecond=0)
                end = (d1.replace(hour=0, minute=0, second=0, microsecond=0)
                       + datetime.timedelta(days=1))
                # Label as the date(s) rather than '00:00–00:00'.
                day0 = start.date().isoformat()
                day1 = (end - datetime.timedelta(days=1)).date().isoformat()
                label = day0 if day0 == day1 else f"{day0} → {day1}"
            else:
                # An HOUR selection -> floor t0 / ceil t1 to the hour; label HH:00.
                start = d0.replace(minute=0, second=0, microsecond=0)
                end = d1.replace(minute=0, second=0, microsecond=0) + \
                    datetime.timedelta(hours=1)
                if end <= start:
                    end = start + datetime.timedelta(hours=1)
                label = None  # build_autopsy derives 'HH:00–HH:00' from the ISOs
            start_iso = start.isoformat()
            end_iso = end.isoformat()
            metrics = ["total_usage", "request_count", "cached_tokens",
                       "reasoning_tokens", "usage_cache"]
            parsed = self.query(metrics, ["model", "provider"], "hour",
                                start_iso, end_iso)
            if parsed is None:
                return None
            meta = parsed.get("metadata") or {}
            report = build_autopsy(parsed.get("rows") or [], start_iso, end_iso,
                                   label=label)
            if meta.get("truncated"):
                report = replace(report, truncated=True)
            return report
        except Exception:
            log.exception("get_autopsy crashed")
            return None


class APIClient:
    """Synchronous API client for OpenRouter."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.last_error: Optional[str] = None

    def get_key_info(self) -> Optional[KeyInfo]:
        try:
            resp = self.session.get(API_KEY_ENDPOINT, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})

            total_credits = 0.0
            total_usage = 0.0
            credits_resp = self.session.get(CREDITS_ENDPOINT, timeout=15)
            if credits_resp.status_code == 200:
                cdata = credits_resp.json().get("data", {})
                total_credits = cdata.get("total_credits", 0.0)
                total_usage = cdata.get("total_usage", 0.0)

            self.last_error = None
            return KeyInfo(
                label=data.get("label", ""),
                limit=data.get("limit"),
                limit_remaining=data.get("limit_remaining"),
                limit_reset=data.get("limit_reset"),
                usage=data.get("usage", 0.0),
                usage_daily=data.get("usage_daily", 0.0),
                usage_weekly=data.get("usage_weekly", 0.0),
                usage_monthly=data.get("usage_monthly", 0.0),
                is_free_tier=data.get("is_free_tier", False),
                total_credits=total_credits,
                total_usage=total_usage,
                raw=data,
            )
        except requests.exceptions.ConnectionError as e:
            self.last_error = "No network"
            log.warning("key_info: connection error: %s", e)
        except requests.exceptions.Timeout as e:
            self.last_error = "Request timed out"
            log.warning("key_info: timed out: %s", e)
        except requests.exceptions.HTTPError as e:
            resp = e.response
            code = resp.status_code if resp is not None else None
            self.last_error = f"HTTP {code}" if code else "HTTP error"
            # Log the real detail so a future "HTTP ?" is debuggable from the
            # log instead of a bare status. Body is truncated.
            body = (resp.text[:300] if resp is not None else "")
            log.warning("key_info: HTTP error status=%s url=%s body=%r",
                        code, getattr(resp, "url", ""), body)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("key_info: unexpected failure", exc_info=True)
        return None

    def get_models(self) -> list:
        try:
            resp = self.session.get(MODELS_ENDPOINT, timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            models = []
            for m in data:
                pricing = m.get("pricing", {})
                pp = _as_float(pricing.get("prompt", "0"))
                cp = _as_float(pricing.get("completion", "0"))
                models.append(ModelInfo(
                    id=m.get("id", ""),
                    name=m.get("name", m.get("id", "")),
                    pricing_prompt=pp,
                    pricing_completion=cp,
                    context_length=m.get("context_length", 0),
                ))
            return models
        except Exception as e:
            log.warning("models fetch failed: %s", e)
            return []

    def get_model_endpoints(self, model_id: str) -> Optional[ModelEndpoints]:
        """Fetch per-provider data for a single model."""
        try:
            url = f"{MODELS_ENDPOINT}/{model_id}/endpoints"
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return parse_model_endpoints(model_id, data)
        except Exception as e:
            log.warning("endpoints(%s) fetch failed: %s", model_id, e)
            return None

    def get_benchmarks(self) -> Optional[BenchmarkBoard]:
        """Fetch DesignArena (+ Artificial Analysis) standings and build the
        Arena board. Slow-moving data; poll infrequently."""
        try:
            da = self.session.get(
                f"{BENCHMARKS_ENDPOINT}?source=design-arena", timeout=20)
            da.raise_for_status()
            da_rows = da.json().get("data", [])
            aa_rows = []
            try:
                aa = self.session.get(
                    f"{BENCHMARKS_ENDPOINT}?source=artificial-analysis", timeout=20)
                if aa.status_code == 200:
                    aa_rows = aa.json().get("data", [])
            except Exception as e:
                log.warning("benchmarks(AA) fetch failed: %s", e)
            return parse_benchmarks(da_rows, aa_rows)
        except Exception as e:
            log.warning("benchmarks fetch failed: %s", e)
            return None

    def get_task_classifications(self):
        """#18 THE COURT — the WORLD's task board: which model the whole of
        OpenRouter reaches for in each macro-category (code/agent/data/general).
        USER-key-gated (sibling to get_benchmarks; rides this client's USER-auth
        self.session — recon proves it 401s noauth, so it is NOT a FrontendClient
        no-auth call). The window param ONLY accepts '7d' (any other value ->
        rejected), so it is HARDCODED. Returns a TaskBoard (parsed) or None on
        failure so the court degrades to its 'world task board unavailable' slate
        without sinking the climb.

        IMPORTANT (decision C): this is GLOBAL market-share of ALL OpenRouter
        traffic, NOT the user's personal task split (scope params are ignored and
        analytics has no task dimension). The widget frames every crown as 'the
        world', NEVER as the user's own mix."""
        try:
            from task_court import parse_task_classifications
            url = f"{BASE_URL}/api/v1/classifications/task"
            # window=7d is the ONLY accepted value (hardcoded — decision A/E).
            resp = self.session.get(url, params={"window": "7d"}, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return parse_task_classifications(data)
        except Exception as e:
            log.warning("classifications/task fetch failed: %s", e)
            return None


class APIWorker(QObject):
    """Background worker that fetches data and emits signals."""
    key_info_ready = Signal(object)
    models_ready = Signal(object)
    endpoints_ready = Signal(str, object)   # (model_id, ModelEndpoints|None)
    benchmarks_ready = Signal(object)       # BenchmarkBoard | None
    provider_trust_ready = Signal(object)   # ProviderTrustBook | None  (no-auth)
    speed_board_ready = Signal(object)      # SpeedBoard | None  (no-auth, #4)
    trend_ready = Signal(object)            # TrendBoard | None  (no-auth, #7 THE TAPE)
    permaslug_resolver_ready = Signal(object)  # PermaslugResolver | None (no-auth)
    uptime_ready = Signal(str, object)      # (model_id, {ep_ident: UptimeHistory}) (no-auth, #3)
    spend_ready = Signal(object)            # SpendBoard | None (mgmt-key analytics, Wave 2 F3/#9)
    insights_ready = Signal(object)         # InsightsBoard | None (Wave 3 INSIGHTS zone; #16/#17/#18 mgmt)
    autopsy_ready = Signal(str, object)     # (token, AutopsyReport|None) — #11, interaction-fired
    logo_ready = Signal(str, object, bool)  # (slug, raw_bytes|None, is_svg)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.client = APIClient()
        # The no-auth frontend client (foundation F2) rides on the same worker
        # thread; it carries its own session (no key, browser-ish UA).
        from frontend_client import FrontendClient
        self.frontend = FrontendClient()
        # F3 analytics client (Wave 2): a SEPARATE mgmt-key session; read-only
        # ground-truth spend. Returns None when no management key is set.
        self.analytics = AnalyticsClient()

    @Slot()
    def fetch_key_info(self):
        try:
            info = self.client.get_key_info()
            if info is None:
                self.error.emit(self.client.last_error or "Unknown error")
            else:
                self.key_info_ready.emit(info)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def fetch_models(self):
        try:
            models = self.client.get_models()
            self.models_ready.emit(models)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def fetch_benchmarks(self):
        """Fetch the Arena board. Always emits (None on failure) so the cards
        can clear/keep their last-good crest without blocking."""
        try:
            board = self.client.get_benchmarks()
            self.benchmarks_ready.emit(board)
        except Exception:
            log.exception("benchmarks worker crashed")
            self.benchmarks_ready.emit(None)

    @Slot()
    def fetch_spend(self):
        """F3/#9: fetch the ground-truth Spend board via the mgmt-key analytics
        API. Always emits (None on failure OR when locked) so the Spend zone
        keeps last-good / shows its locked state, never crashes."""
        try:
            board = self.analytics.get_spend_board()
            if board is not None:
                sp = board.spectrum
                spike = (sp.buckets[sp.spike_index]
                         if 0 <= sp.spike_index < len(sp.buckets) else "n/a")
                # INFO line so the live boot can confirm the board landed.
                # Magnitudes only here (a $ total) — never the key.
                log.info("spend board: %d models, $%.2f over %s, spike %s",
                         len(sp.models), sp.total, board.range_label.lower(), spike)
            else:
                log.info("spend board: none (locked or no data)")
            self.spend_ready.emit(board)
        except Exception:
            log.exception("spend worker crashed")
            self.spend_ready.emit(None)

    @Slot()
    def fetch_insights(self):
        """Wave 3 INSIGHTS zone: fetch the InsightsBoard for the mgmt features
        (#16/#17/#18). Modeled on fetch_spend — try/except, ALWAYS emit (None on
        failure / locked) so the zone keeps last-good / shows its locked state and
        never crashes. NOTE: #15 THE ASSAY is NOT served here (it rides
        _distribute_value off already-fetched USER-key stores); only the mgmt
        slots are populated here. The management key is used read-only by the
        queries and is NEVER logged.

        #16 THE TITLE BELT populates .week now: ONE weekly dims=[model] query over
        the last 21d (total_usage / tokens_total / request_count). This exact
        query is CACHED by its (metrics,dims,gran,start,end) key, so #18 will
        re-hit it for FREE later. #17 THE FLIGHT RECORDER populates .recorder: ONE
        daily dims=[] full-range query over the last 60d — its dims=[] total is
        ALSO REUSED by #18 for the user's token total (decision A). A failure in
        EITHER build degrades ONLY its own slot to None (independent try/except),
        so #17's failure never wipes #16's week and vice-versa. .court (#18) stays
        None until that feature adds its query."""
        try:
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)

            # ---- #16 THE TITLE BELT — weekly dims=[model] (reused by #18) ----
            week = None
            try:
                from model_of_week import build_model_of_week
                w_start = now - datetime.timedelta(days=21)
                parsed_w = self.analytics.query(
                    ["total_usage", "tokens_total", "request_count"],
                    ["model"], "week", w_start.isoformat(), now.isoformat())
                if parsed_w is not None:
                    week = build_model_of_week(parsed_w.get("rows") or [])
            except Exception:
                # #16 must never sink the whole board — degrade its slot to None.
                log.exception("title belt build failed")
                week = None

            # ---- #17 THE FLIGHT RECORDER — daily dims=[] full-range (reused #18)
            recorder = None
            try:
                from token_recorder import build_token_recorder
                r_start = now - datetime.timedelta(days=60)
                # The #17 daily dims=[] query — its dims=[] total re-hits the cache
                # for #18 for FREE (decision A). dims=[] -> bucket key date__day.
                parsed_d = self.analytics.query(
                    ["total_usage", "tokens_total", "request_count"],
                    [], "day", r_start.isoformat(), now.isoformat())
                if parsed_d is not None:
                    recorder = build_token_recorder(parsed_d.get("rows") or [])
            except Exception:
                # #17 must never sink the whole board — degrade its slot to None.
                log.exception("flight recorder build failed")
                recorder = None

            # ---- #18 THE COURT & THE CLIMB — the wide closer (decision B) ----
            # Rides this same fetch (no new worker slot). It REUSES #16's weekly
            # top model + #17's lifetime token total from the AnalyticsClient
            # cache (the identical query args re-hit the cache for FREE) and adds
            # the two NEW network methods it owns: get_task_classifications()
            # (USER-auth, window=7d) + get_rankings_apps() (NOAUTH browser-UA).
            # A failure in ANY #18 source degrades ONLY board.court to None WITHOUT
            # wiping #16's .week or #17's .recorder (independent try/except), and a
            # PER-HALF degrade lives inside build_court_climb (court vs climb).
            court = None
            try:
                from task_court import build_court_climb

                # (a) THE COURT — the WORLD task board (USER/mgmt; None -> the
                # court band collapses to 'world task board unavailable').
                task_board = None
                try:
                    task_board = self.client.get_task_classifications()
                except Exception:
                    log.exception("court: classifications fetch failed")
                    task_board = None

                # (c) THE CLIMB — the public apps ladder (NOAUTH browser-UA;
                # almost always renders). None -> the climb half can't render.
                apps = None
                try:
                    apps = self.frontend.get_rankings_apps()
                except Exception:
                    log.exception("climb: rankings/apps fetch failed")
                    apps = None

                # (b) REUSE #16's weekly top model + #17's token total + the
                # dims=[app] label from the analytics CACHE (free hits — same
                # args as the #16/#17 queries above). The ember overlay needs the
                # top model (mgmt); when locked these all return None -> the
                # ember is dropped, the world court + summit still render.
                top_model = None
                if week is not None and not week.is_empty:
                    top_model = week.champion_id            # reuse #16 (no query)
                user_tokens = recorder.lifetime_tokens if recorder is not None else 0
                user_app = ""
                try:
                    # dims=[app] over the SAME 60d window — a small extra mgmt
                    # query for the 'your app' label ('OpenCode'). Degrades to ''.
                    parsed_app = self.analytics.query(
                        ["total_usage", "tokens_total", "request_count"],
                        ["app"], "day", r_start.isoformat(), now.isoformat())
                    if parsed_app is not None:
                        agg_app: dict = {}
                        for row in (parsed_app.get("rows") or []):
                            label = str(row.get("app") or "")
                            if not label:
                                continue
                            agg_app[label] = agg_app.get(label, 0) + _as_int(
                                row.get("tokens_total"))
                        if agg_app:
                            user_app = max(agg_app.items(), key=lambda kv: kv[1])[0]
                except Exception:
                    log.exception("court: dims=[app] label query failed")
                    user_app = ""

                court = build_court_climb(
                    task_board, apps, top_model, user_tokens, user_app)
            except Exception:
                # #18 must never sink the whole board — degrade its slot to None.
                log.exception("court & climb build failed")
                court = None

            board = InsightsBoard(week=week, recorder=recorder, court=court)

            # INFO lines so the live boot can confirm each slot landed. Magnitudes
            # only (ids/shares/counts) — NEVER the management key.
            if week is not None and not week.is_empty:
                log.info("title belt: champion=%s share=%.0f%% week=%d",
                         week.champion_id, week.share_pct, week.week_count)
            elif week is not None:
                log.info("title belt: no spend this week (empty)")
            else:
                log.info("title belt: none (locked or query failed)")

            if recorder is not None and not recorder.is_empty:
                log.info("flight recorder: lifetime=%d tok, record=%s $%.2f, run=%d",
                         recorder.lifetime_tokens, recorder.record.date,
                         recorder.record.spend, recorder.streak_run)
            elif recorder is not None:
                log.info("flight recorder: no traffic logged yet (empty)")
            else:
                log.info("flight recorder: none (locked or query failed)")

            if court is not None and not court.is_empty:
                # crowns=N, apps floor=Ntok, you=Mtok (Xx below) — the honest
                # headline of the whole feature (NEVER an 'out-tokened' claim).
                gap = (f"{court.gap_multiple:,.0f}x below" if court.user_in_valley
                       else "on the board")
                log.info("court & climb: crowns=%d, apps floor=%d tok, "
                         "you=%d tok (%s), ember=%s",
                         len(court.seats), court.floor_tokens, court.user_tokens,
                         gap, court.has_ember)
            else:
                log.info("court & climb: none (both sources locked/failed)")

            self.insights_ready.emit(board)
        except Exception:
            log.exception("insights worker crashed")
            self.insights_ready.emit(None)

    @Slot(str, str)
    def fetch_autopsy(self, t0_iso, t1_iso):
        """#11 THE AUTOPSY — interaction-fired (a lasso release), NOT polled and
        NOT part of get_spend_board. Runs the hourly dims=[model,provider] query
        clamped to [floor(t0) hour, ceil(t1) hour], builds the AutopsyReport, and
        ALWAYS emits autopsy_ready(token, report|None) (None on failure / when
        locked) so the dossier never crashes the app. Routed through
        AnalyticsClient.query so it caches by its key (the F3 contract). The
        token (f'{t0}|{t1}') keys the popup debounce. The management key is used
        read-only and is NEVER logged."""
        token = f"{t0_iso}|{t1_iso}"
        try:
            report = self.analytics.get_autopsy(t0_iso, t1_iso)
            if report is not None and not report.is_empty:
                top = report.rows[0]
                log.info(
                    "autopsy: %s %d rows, top=%s@%s $%.2f (%d%%)",
                    report.window_label, len(report.rows),
                    top.short_name, top.provider, top.usage,
                    round(top.share * 100),
                )
            elif report is not None:
                log.info("autopsy: %s clean window ($0 drained)",
                         report.window_label)
            else:
                log.info("autopsy: none (locked or query failed)")
            self.autopsy_ready.emit(token, report)
        except Exception:
            log.exception("autopsy worker crashed")
            self.autopsy_ready.emit(token, None)

    @Slot()
    def fetch_provider_trust(self):
        """Fetch the no-auth all-providers trust/privacy posture (The Ledger).
        Always emits (None on failure) so cards keep their last-good seals."""
        try:
            book = self.frontend.get_provider_trust()
            self.provider_trust_ready.emit(book)
        except Exception:
            log.exception("provider trust worker crashed")
            self.provider_trust_ready.emit(None)

    @Slot()
    def fetch_speed_board(self):
        """Fetch the no-auth rankings/performance fleet (Speed Percentile, #4).
        Always emits (None on failure) so cards keep their last-good band."""
        try:
            board = self.frontend.get_speed_board()
            self.speed_board_ready.emit(board)
        except Exception:
            log.exception("speed board worker crashed")
            self.speed_board_ready.emit(None)

    @Slot()
    def fetch_trend(self):
        """THE TAPE (#7): fetch the no-auth rankings/models week-over-week
        momentum board (~191KB, permaslug-keyed). Always emits (None on failure)
        so cards keep their last-good tape."""
        try:
            board = self.frontend.get_rankings_models()
            self.trend_ready.emit(board)
        except Exception:
            log.exception("trend board worker crashed")
            self.trend_ready.emit(None)

    @Slot()
    def fetch_permaslug_resolver(self):
        """Fetch the no-auth catalog slug↔permaslug map. Needed to resolve a
        pinned model's public slug to the versioned permaslug the speed (and,
        later, uptime) datasets are keyed by. Always emits (None on failure)."""
        try:
            res = self.frontend.get_permaslug_resolver()
            self.permaslug_resolver_ready.emit(res)
        except Exception:
            log.exception("permaslug resolver worker crashed")
            self.permaslug_resolver_ready.emit(None)

    @Slot(str, str)
    def fetch_uptime(self, model_id: str, permaslug: str):
        """THE PULSE (#3): per-endpoint 73h uptime for one pinned model. Uptime
        is PER-ENDPOINT, so this fans out — resolve the permaslug to its serving
        endpoints (stats/endpoint), then fetch uptime-hourly per endpoint UUID.
        No auth (frontend API). ALWAYS emits (an empty/partial dict on failure)
        so cards keep their last-good cardiogram and never blank. Keys the dict
        by the SAME ident the trust seals use (provider_slug a.k.a. the row tag)
        so the right history lands on the right row across refreshes."""
        histories = {}
        try:
            if not permaslug:
                self.uptime_ready.emit(model_id, histories)
                return
            refs = self.frontend.get_endpoint_refs(permaslug)
            # GUARD the resolver-returns-slug-unchanged 404: some models (e.g.
            # anthropic/claude-3.5-sonnet) resolve to an unversioned slug whose
            # stats/endpoint 404s → get_endpoint_refs returns [] (it never
            # raises). We just emit an empty dict and skip — never crash.
            got = 0
            for ref in refs:
                if not ref.id:
                    continue
                hist = self.frontend.get_uptime_hourly(ref.id)
                if hist is None:
                    continue
                ident = ref.provider_slug or ref.provider_name
                if not ident:
                    continue
                histories[ident] = hist
                got += 1
            log.info("uptime fetch for %s: %d endpoints, %d with history",
                     model_id, len(refs), got)
        except Exception:
            log.exception("uptime worker crashed for %s", model_id)
        # A single greppable INFO line on the dedicated pulse logger so the live
        # boot check has something deterministic to assert lands.
        logging.getLogger("pulse.openrouter").info(
            "PULSE uptime landed for %s: %d endpoints with history",
            model_id, len(histories))
        self.uptime_ready.emit(model_id, histories)

    @Slot(str, str)
    def fetch_logo(self, slug: str, url: str):
        """Download one provider's raw logo bytes (no Qt). Always emits so the
        store can drop the slug from its pending set even on failure."""
        try:
            from logo_store import download_logo
            res = download_logo(url, self.frontend.session)
            if res is None:
                self.logo_ready.emit(slug, None, False)
            else:
                data, is_svg = res
                self.logo_ready.emit(slug, data, is_svg)
        except Exception:
            log.exception("logo worker crashed for %s", slug)
            self.logo_ready.emit(slug, None, False)

    @Slot(str)
    def fetch_endpoints(self, model_id: str):
        """Fetch endpoints for one model. Always emits, even on failure
        (so the section can show a per-row error state)."""
        try:
            ep = self.client.get_model_endpoints(model_id)
            self.endpoints_ready.emit(model_id, ep)
        except Exception:
            log.exception("endpoints(%s) worker crashed", model_id)
            self.endpoints_ready.emit(model_id, None)