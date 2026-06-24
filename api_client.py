"""
OpenRouter Pulse - API Client
Handles all communication with OpenRouter API endpoints.
"""
import logging
import re
import requests
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Optional
from PySide6.QtCore import QObject, Signal, Slot, QThread

from config import (
    API_KEY, API_KEY_ENDPOINT, MODELS_ENDPOINT,
    MODELS_COUNT_ENDPOINT, STATUS_URL,
    ANALYTICS_META_ENDPOINT, ANALYTICS_QUERY_ENDPOINT,
)

log = logging.getLogger("pulse.api")

BASE_URL = "https://openrouter.ai"
CREDITS_ENDPOINT = f"{BASE_URL}/api/v1/credits"
PROVIDERS_ENDPOINT = f"{BASE_URL}/api/v1/providers"
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


@dataclass
class ServiceStatus:
    chat_api: str = "unknown"
    data_api: str = "unknown"
    homepage: str = "unknown"
    overall: str = "unknown"


@dataclass
class ProviderInfo:
    name: str
    slug: str
    status_page_url: Optional[str] = None
    headquarters: Optional[str] = None


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
        try:
            pp = float(pricing.get("prompt", "0"))
        except (ValueError, TypeError):
            pp = 0.0
        try:
            cp = float(pricing.get("completion", "0"))
        except (ValueError, TypeError):
            cp = 0.0
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


# ===========================================================================
#  F3 — Analytics (ground-truth spend). The foundation the whole Spend zone
#  rides. Keyed on the MANAGEMENT key (config.API_KEY/HEADERS carry only the
#  regular user key); analytics is mgmt-key-gated. Pure parsers are module-
#  level + unit-tested against the verbatim recon row.
# ===========================================================================

# The day/hour/week bucket key NAME is NOT stable across dimension sets:
#   dims=[] or [model]      -> date__day / date__hour / date__week
#   dims=[model, provider]  -> created_at__day / created_at__hour / ...
# So NEVER hardcode date__day — detect the single key matching this regex.
_BUCKET_KEY_RE = re.compile(r"^(date|created_at)__(minute|hour|day|week|month)$")


def _as_float(v) -> float:
    """Coerce an analytics metric to float. total_usage/usage_cache/
    cache_hit_rate come back as JSON numbers, but be defensive — some metrics
    arrive as JSON strings. None/'' -> 0.0."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _as_int(v) -> int:
    """Coerce an analytics COUNT metric to int. request_count and ALL tokens_*
    arrive as JSON STRINGS ("1","8685"); coerce via float() first so "1.0" and
    1.0 both work. None/'' -> 0."""
    if v is None or v == "":
        return 0
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _bucket_key(row: dict):
    """Return the row's time-bucket key (date__day / created_at__hour / ...)
    by regex, or None if absent. Detects the name so the parser works for both
    dims=[model] (date__*) and dims=[model,provider] (created_at__*) shapes."""
    for k in row:
        if _BUCKET_KEY_RE.match(k):
            return k
    return None


def parse_analytics_query(envelope: Optional[dict]) -> dict:
    """Unwrap the analytics envelope into a plain dict:
        {"rows": [...], "metadata": {...}, "cachedAt": <epoch ms|None>}
    The envelope shape (confirmed live) is the OUTER {"data": {...}} already
    unwrapped to its inner object: {"data":[rows], "metadata":{...},
    "cachedAt":<ms>}. Tolerates a fully-wrapped envelope too. Never raises."""
    if not isinstance(envelope, dict):
        return {"rows": [], "metadata": {}, "cachedAt": None}
    inner = envelope
    # Tolerate being handed the raw HTTP json (one extra "data" wrap).
    if "rows" not in inner and isinstance(inner.get("data"), dict) and \
            isinstance(inner["data"].get("data"), list):
        inner = inner["data"]
    rows = inner.get("data")
    if not isinstance(rows, list):
        rows = inner.get("rows") if isinstance(inner.get("rows"), list) else []
    meta = inner.get("metadata") if isinstance(inner.get("metadata"), dict) else {}
    return {"rows": rows, "metadata": meta, "cachedAt": inner.get("cachedAt")}


def _short_model(model_id: str) -> str:
    """Strip the vendor/ prefix for a compact legend label
    (anthropic/claude-4.6-sonnet-20260217 -> claude-4.6-sonnet-20260217)."""
    if not model_id:
        return ""
    return model_id.split("/", 1)[1] if "/" in model_id else model_id


@dataclass(frozen=True)
class SpendModel:
    """One model's roll-up across the range (#9 legend-spine row)."""
    model_id: str
    short_name: str
    total_usage: float
    request_count: int
    share: float          # fraction 0..1 of the range total


@dataclass(frozen=True)
class SpendSpectrumData:
    """#9 THE SPECTRUM payload: a per-bucket per-model spend matrix plus the
    descending-spend model roll-up, the hero range total, and the spike bucket.

    buckets: ordered list of bucket labels (the date__day / __hour strings).
    matrix:  {model_id: [usage_per_bucket]} aligned to `buckets` (0 where absent).
    models:  descending-spend SpendModel list (rank 0 == heaviest == bottom band).
    """
    buckets: tuple
    matrix: dict
    models: tuple                 # tuple[SpendModel], descending spend
    total: float                  # hero range total ($)
    granularity: str
    spike_index: int              # index into buckets of the max-total bucket (-1 none)
    spike_total: float
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return self.total <= 0.0 or not self.models


@dataclass(frozen=True)
class SpendBoard:
    """The single aggregate the analytics fetch returns; the dashboard
    distributes it to the Spend widgets. #9 populates .spectrum NOW; the other
    slots are reserved/empty for the later Spend features (#10/#12/#13/#14).
    `start`/`end` are the range ISO strings; `range_label` is the human header."""
    spectrum: SpendSpectrumData
    start: str = ""
    end: str = ""
    range_label: str = "Last 7 Days"
    # -- reserved for later Spend features (filled by #10/#12/#13/#14) --
    receipts: tuple = ()
    savings: Optional[object] = None
    ghosts: Optional[object] = None
    budget: Optional[object] = None


def build_spend_spectrum(rows: list, granularity: str = "day",
                         truncated: bool = False) -> SpendSpectrumData:
    """Pure builder: the per-bucket per-model matrix + descending-spend model
    roll-up + hero total + spike bucket. Honors the data quirks:
      - bucket key detected via _bucket_key (date__day OR created_at__*)
      - request_count/tokens via _as_int (STRINGS); total_usage via _as_float
      - divide-by-zero guarded (share 0 when total==0)
    """
    rows = rows or []
    # Collect ordered buckets + per-(model,bucket) usage + per-model totals.
    bucket_order: list = []
    seen_buckets = set()
    per_model_usage: dict = defaultdict(float)
    per_model_reqs: dict = defaultdict(int)
    cell: dict = defaultdict(float)        # (model, bucket) -> usage

    for row in rows:
        if not isinstance(row, dict):
            continue
        bk = _bucket_key(row)
        bucket = row.get(bk) if bk else None
        model = row.get("model") or ""
        usage = _as_float(row.get("total_usage"))
        reqs = _as_int(row.get("request_count"))
        if bucket is not None and bucket not in seen_buckets:
            seen_buckets.add(bucket)
            bucket_order.append(bucket)
        per_model_usage[model] += usage
        per_model_reqs[model] += reqs
        if bucket is not None:
            cell[(model, bucket)] += usage

    bucket_order.sort()  # chronological (ISO date/hour strings sort correctly)
    total = sum(per_model_usage.values())

    # Descending-spend model roll-up (heaviest first => rank 0 => bottom band).
    ordered_models = sorted(
        per_model_usage.keys(),
        key=lambda m: (-per_model_usage[m], m),
    )
    models = tuple(
        SpendModel(
            model_id=m,
            short_name=_short_model(m),
            total_usage=per_model_usage[m],
            request_count=per_model_reqs[m],
            share=(per_model_usage[m] / total) if total > 0 else 0.0,
        )
        for m in ordered_models
    )

    matrix = {
        m: [cell.get((m, b), 0.0) for b in bucket_order]
        for m in ordered_models
    }

    # Spike = the bucket with the largest summed spend across all models.
    spike_index = -1
    spike_total = 0.0
    if bucket_order:
        bucket_totals = [
            sum(matrix[m][i] for m in ordered_models)
            for i in range(len(bucket_order))
        ]
        spike_index = max(range(len(bucket_totals)), key=lambda i: bucket_totals[i])
        spike_total = bucket_totals[spike_index]

    return SpendSpectrumData(
        buckets=tuple(bucket_order),
        matrix=matrix,
        models=models,
        total=total,
        granularity=granularity,
        spike_index=spike_index,
        spike_total=spike_total,
        truncated=bool(truncated),
    )


# ===========================================================================
#  #11 THE AUTOPSY — the interaction-fired spend cause-of-death sheet (pure)
# ===========================================================================
# Fired ONLY on a lasso release (NOT in get_spend_board, NOT polled). The pure
# builder decomposes a lassoed hour window into per-(model,provider) $ rows so a
# $4 afternoon reads as "sonnet @ Anthropic · 87 reqs · $4.14 · 93% of the
# spike". Honors every analytics quirk: bucket key detected via _bucket_key
# (created_at__hour for provider dims), request_count/cached_tokens STRINGS via
# _as_int, and usage_cache NEGATIVE = a caching OFFSET (a GREEN credit, NEVER a
# drain) -> the report carries it as a POSITIVE magnitude `cache_offset`.
AUTOPSY_MAX_ROWS = 6   # rows beyond this collapse into a bounded "+N more" bar


def _hour_label(iso: str) -> str:
    """'HH:00' from an ISO-ish timestamp. Accepts the lasso ISOs
    ('2026-06-22T12:00:00+00:00') AND the live created_at__hour space form
    ('2026-06-22 12:00:00'); falls back to the raw string if unparseable."""
    if not iso:
        return "··:00"
    s = str(iso)
    # Find the 'T' or space separator, then read the HH that follows.
    sep = -1
    if "T" in s:
        sep = s.index("T")
    elif " " in s:
        sep = s.index(" ")
    if sep >= 0 and len(s) >= sep + 3:
        hh = s[sep + 1:sep + 3]
        if hh.isdigit():
            return f"{int(hh):02d}:00"
    return s


@dataclass(frozen=True)
class AutopsyRow:
    """One (model,provider) incision: the $ it drained from the lassoed window,
    its request count, and its share of the spike total."""
    model_id: str
    short_name: str
    provider: str
    usage: float          # $ drained in the window
    request_count: int    # calls in the window
    share: float          # fraction 0..1 of the window total


@dataclass(frozen=True)
class AutopsyReport:
    """#11 THE AUTOPSY payload built from the hourly dims=[model,provider] query
    clamped to the lassoed window. `rows` is the FULL descending-$ list;
    `visible` is the first AUTOPSY_MAX_ROWS and `remainder_*` collapse the tail
    into one bounded bar (so the dossier pixmap height stays bounded).

    Honesty contract (decision D):
      - spike_total = Σ total_usage over the window; row.share = usage/spike_total.
      - cache_offset = Σ abs(usage_cache) (usage_cache is NEGATIVE = a realized
        cache credit) -> a POSITIVE magnitude, shown GREEN, NEVER a drain.
      - request_total / cached_total are COUNTS (request_count/cached_tokens are
        STRINGS in the API -> _as_int).
      - window_label is 'HH:00–HH:00' (an en-dash); t0/t1 are the raw ISOs.
    """
    rows: tuple                  # tuple[AutopsyRow], descending by usage
    visible: tuple               # tuple[AutopsyRow], the first AUTOPSY_MAX_ROWS
    remainder_count: int         # how many rows collapsed into the remainder bar
    remainder_usage: float       # Σ usage of the collapsed tail ($)
    spike_total: float           # Σ total_usage over the window ($)
    request_total: int           # Σ request_count over the window
    cached_total: int            # Σ cached_tokens over the window (a COUNT)
    cache_offset: float          # Σ abs(usage_cache) -> POSITIVE magnitude ($), GREEN
    window_label: str            # 'HH:00–HH:00'
    t0: str                      # the lassoed window start ISO (raw)
    t1: str                      # the lassoed window end ISO (raw)
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        # Key present but $0 drained in the lassoed window -> the "clean window"
        # state (one muted bar, no crimson). A real populated-zero, NOT locked.
        return self.spike_total <= 0.0 or not self.rows


def build_autopsy(rows: list, t0_iso: str, t1_iso: str,
                  label: Optional[str] = None) -> AutopsyReport:
    """Pure builder for #11 — aggregate the hourly dims=[model,provider] rows of
    the lassoed window into per-(model,provider) incisions, descending by $.

    - bucket key detected via _bucket_key (created_at__hour for provider dims);
      we don't filter on it (the query is already clamped to the window) but it
      proves the row shape and is exercised by the tests.
    - total_usage via _as_float; request_count/cached_tokens via _as_int
      (STRINGS); usage_cache NEGATIVE -> cache_offset = Σ abs(usage_cache).
    - share guarded against divide-by-zero (0 when spike_total==0).
    - rows beyond AUTOPSY_MAX_ROWS collapse into a single remainder bar.
    - `label` overrides the window label (the client passes a date span for a
      DAY-granularity selection); when None we derive 'HH:00–HH:00' from the ISOs.
    """
    rows = rows or []
    per_pair: dict = defaultdict(lambda: {"usage": 0.0, "reqs": 0})
    request_total = 0
    cached_total = 0
    cache_offset = 0.0
    spike_total = 0.0

    for row in rows:
        if not isinstance(row, dict):
            continue
        model = row.get("model") or ""
        provider = row.get("provider") or ""
        usage = _as_float(row.get("total_usage"))
        reqs = _as_int(row.get("request_count"))
        per_pair[(model, provider)]["usage"] += usage
        per_pair[(model, provider)]["reqs"] += reqs
        request_total += reqs
        cached_total += _as_int(row.get("cached_tokens"))
        # usage_cache is NEGATIVE = a saving; abs() -> a positive offset magnitude
        # (an occasional positive 1-request value is handled by abs too).
        cache_offset += abs(_as_float(row.get("usage_cache")))
        spike_total += usage

    ordered = sorted(
        per_pair.items(),
        key=lambda kv: (-kv[1]["usage"], kv[0][0], kv[0][1]),
    )
    all_rows = tuple(
        AutopsyRow(
            model_id=model,
            short_name=_short_model(model),
            provider=provider,
            usage=v["usage"],
            request_count=v["reqs"],
            share=(v["usage"] / spike_total) if spike_total > 0 else 0.0,
        )
        for (model, provider), v in ordered
    )

    visible = all_rows[:AUTOPSY_MAX_ROWS]
    tail = all_rows[AUTOPSY_MAX_ROWS:]
    remainder_count = len(tail)
    remainder_usage = sum(r.usage for r in tail)

    if label is None:
        label = f"{_hour_label(t0_iso)}–{_hour_label(t1_iso)}"   # en-dash

    return AutopsyReport(
        rows=all_rows,
        visible=visible,
        remainder_count=remainder_count,
        remainder_usage=remainder_usage,
        spike_total=spike_total,
        request_total=request_total,
        cached_total=cached_total,
        cache_offset=cache_offset,
        window_label=label,
        t0=t0_iso or "",
        t1=t1_iso or "",
    )


# --- #10 THE TILL ROLL noise-gate floors -------------------------------------
# A "PRICE UP / DOWN" stamp fires only when ALL gates pass, so a 1-request day
# at a weird $/call can't trigger a false alarm. Floors picked from the LIVE
# data (the real spike day was 95 requests / ~$0.046 a call; the noise days are
# 1-6 requests). MIN_STAMP_REQUESTS=10 keeps the 95-req spike but kills 1-6 req
# days; MIN_STAMP_PERCALL=$0.0005 is below the real spike yet above haiku noise.
RECEIPT_SPIKE_MULT = 2.0          # latest >= 2x median -> "PRICE UP"
RECEIPT_DROP_MULT = 0.5           # latest <= 0.5x median -> "PRICE DOWN" (green)
RECEIPT_MIN_STAMP_REQUESTS = 10   # latest-day request_count floor
RECEIPT_MIN_STAMP_PERCALL = 0.0005  # latest-day $/call absolute floor
RECEIPT_MIN_HISTORY_DAYS = 7      # need a full week before a median is trustworthy


@dataclass(frozen=True)
class Receipt:
    """One model's per-call receipt (#10 THE TILL ROLL).

    All money is from REAL analytics totals — NEVER a fabricated per-line split.
    The line items show real AVERAGE token COUNTS per call (input/output/
    reasoning); the only itemized $ are the cache CREDIT (abs(usage_cache)/calls,
    a saving) and the SUBTOTAL/CALL (total_usage/calls). `spark` is the 7 daily
    $/call values for the micro-sparkline. The stamp (`stamp_mult` + `stamp_dir`)
    fires only through the noise gate; `young` suppresses it on a <7-day account.
    """
    model_id: str
    short_name: str
    total_usage: float            # range total $ (ties back to #9)
    request_count: int            # range total calls
    per_call: float               # avg $/call over the range (guarded /0)
    # per-call AVERAGE token counts (NO $ — decision A)
    avg_prompt_tok: int
    avg_completion_tok: int
    avg_reasoning_tok: int
    avg_cached_tok: int
    cache_credit_per_call: float  # abs(usage_cache)/calls (a GREEN credit)
    spark: tuple                  # tuple[float] daily $/call (chronological)
    # the always-on tripwire stamp
    stamp_mult: float             # latest/median multiplier (0.0 when no stamp)
    stamp_dir: int                # +1 PRICE UP, -1 PRICE DOWN, 0 none
    young: bool                   # <7 days of history -> stamp suppressed

    @property
    def has_stamp(self) -> bool:
        return self.stamp_dir != 0

    @property
    def is_empty(self) -> bool:
        return self.request_count <= 0 and self.total_usage <= 0.0


def build_receipts(rows: list) -> tuple:
    """Pure builder for #10 — per-model receipts from QUERY A's SAME day rows
    (no new query). Returns a tuple[Receipt] in descending-spend order (so a
    receipt stub stacks under #9's matching legend row).

    Honesty contract (decision A): per-call line items are real AVERAGE token
    COUNTS; the only itemized $ are the cache credit (abs(usage_cache)/calls) and
    the subtotal/call (total_usage/calls). No total_usage is split across
    input/output by token share. The stamp (decision B) compares the latest-day
    $/call to the median of the prior days and fires only when latest >= 2x (or
    <= 0.5x) median AND latest request_count >= MIN_STAMP_REQUESTS AND latest
    $/call >= MIN_STAMP_PERCALL; a <7-day account suppresses the stamp (`young`).
    """
    rows = rows or []
    # Gather per-(model,bucket) day cells + per-model range totals.
    bucket_order: list = []
    seen_buckets = set()
    per_model: dict = defaultdict(lambda: {
        "usage": 0.0, "reqs": 0, "prompt": 0, "compl": 0,
        "reason": 0, "cached": 0, "ucache": 0.0,
    })
    # (model,bucket) -> {usage, reqs} so we can derive the daily $/call series.
    day_cell: dict = defaultdict(lambda: {"usage": 0.0, "reqs": 0})

    for row in rows:
        if not isinstance(row, dict):
            continue
        bk = _bucket_key(row)
        bucket = row.get(bk) if bk else None
        model = row.get("model") or ""
        usage = _as_float(row.get("total_usage"))
        reqs = _as_int(row.get("request_count"))
        if bucket is not None and bucket not in seen_buckets:
            seen_buckets.add(bucket)
            bucket_order.append(bucket)
        agg = per_model[model]
        agg["usage"] += usage
        agg["reqs"] += reqs
        agg["prompt"] += _as_int(row.get("tokens_prompt"))
        agg["compl"] += _as_int(row.get("tokens_completion"))
        agg["reason"] += _as_int(row.get("reasoning_tokens"))
        agg["cached"] += _as_int(row.get("cached_tokens"))
        agg["ucache"] += _as_float(row.get("usage_cache"))
        if bucket is not None:
            c = day_cell[(model, bucket)]
            c["usage"] += usage
            c["reqs"] += reqs

    bucket_order.sort()  # chronological (ISO date strings sort correctly)
    n_days = len(bucket_order)

    # Descending-spend order so a stub sits under #9's matching legend row.
    ordered_models = sorted(
        per_model.keys(),
        key=lambda m: (-per_model[m]["usage"], m),
    )

    def _percall(usage: float, reqs: int) -> float:
        return (usage / reqs) if reqs > 0 else 0.0

    receipts = []
    for model in ordered_models:
        agg = per_model[model]
        reqs = agg["reqs"]
        usage = agg["usage"]
        per_call = _percall(usage, reqs)
        # The 7-tick daily $/call series, aligned chronologically (0 where the
        # model had no calls that day — a guarded divide).
        spark = []
        for b in bucket_order:
            c = day_cell.get((model, b))
            spark.append(_percall(c["usage"], c["reqs"]) if c else 0.0)

        # --- the noise-gated stamp (decision B) ---
        stamp_mult = 0.0
        stamp_dir = 0
        # Need a full week of history before trusting a median basis.
        young = n_days < RECEIPT_MIN_HISTORY_DAYS
        if not young and n_days >= 2:
            latest_cell = day_cell.get((model, bucket_order[-1]))
            latest_pc = (_percall(latest_cell["usage"], latest_cell["reqs"])
                         if latest_cell else 0.0)
            latest_reqs = latest_cell["reqs"] if latest_cell else 0
            prior = spark[:-1]
            # Median over prior days where the model actually had calls (a $0
            # day with no calls is not a real cheaper "price", just silence).
            prior_active = [v for v in prior if v > 0.0]
            gates_ok = (
                latest_reqs >= RECEIPT_MIN_STAMP_REQUESTS
                and latest_pc >= RECEIPT_MIN_STAMP_PERCALL
                and len(prior_active) >= 1
            )
            if gates_ok:
                med = statistics.median(prior_active)
                if med > 0.0:
                    mult = latest_pc / med
                    if mult >= RECEIPT_SPIKE_MULT:
                        stamp_mult = mult
                        stamp_dir = 1
                    elif mult <= RECEIPT_DROP_MULT:
                        stamp_mult = mult
                        stamp_dir = -1

        receipts.append(Receipt(
            model_id=model,
            short_name=_short_model(model),
            total_usage=usage,
            request_count=reqs,
            per_call=per_call,
            avg_prompt_tok=(agg["prompt"] // reqs) if reqs > 0 else 0,
            avg_completion_tok=(agg["compl"] // reqs) if reqs > 0 else 0,
            avg_reasoning_tok=(agg["reason"] // reqs) if reqs > 0 else 0,
            avg_cached_tok=(agg["cached"] // reqs) if reqs > 0 else 0,
            cache_credit_per_call=_percall(abs(agg["ucache"]), reqs),
            spark=tuple(spark),
            stamp_mult=stamp_mult,
            stamp_dir=stamp_dir,
            young=young,
        ))
    return tuple(receipts)


# --- #12 THE REBATE STUB (cache & reasoning savings) -------------------------

@dataclass(frozen=True)
class SavingsModel:
    """One model's rebate breakdown row for the per-model popup (#12)."""
    model_id: str
    short_name: str
    rebate: float          # abs(usage_cache) summed over the range ($, a CREDIT)
    cached_tokens: int     # cache-read tokens summed over the range (a COUNT)
    hit_rate_pct: float    # request-weighted hit-rate for this model, [0,100]
    reasoning_tokens: int  # reasoning tokens summed (a COUNT, NEVER a $)


@dataclass(frozen=True)
class Savings:
    """#12 THE REBATE STUB payload — derived from QUERY A's SAME day rows (no new
    query). The headline `total_rebate` is the realized cache CREDIT already
    applied to the balance, NOT a hypothetical.

    Honesty contract baked in (decision B):
      - usage_cache is NEGATIVE = a saving (occasionally POSITIVE on a 1-request
        day) -> total_rebate = Σ abs(usage_cache).
      - cache_hit_rate is a 0..1 FRACTION -> ×100 AT PARSE so the label reads
        "93.6%", never "0.94%". hit_rate_pct is in [0,100].
      - cached_tokens/reasoning_tokens/request_count are STRINGS -> _as_int.
      - reasoning_total is a COUNT (no reasoning-$ metric exists) -> it drives
        ONLY the purple meter + the count label, NEVER a dollar figure.
    `spark` is the daily abs(usage_cache) series (chronological) for the popup
    sparkline; `reasoning_ref` is the max single-day reasoning count, the
    normalize basis for the meter fill (guarded /0)."""
    total_rebate: float          # Σ abs(usage_cache) over the range ($)
    hit_rate_pct: float          # request-weighted mean hit-rate, [0,100]
    reasoning_total: int         # Σ reasoning_tokens over the range (a COUNT)
    models: tuple                # tuple[SavingsModel] sorted by rebate desc
    spark: tuple                 # tuple[float] daily abs(usage_cache) (chrono)
    reasoning_ref: int           # max daily reasoning count (meter normalize)

    @property
    def is_empty(self) -> bool:
        # No realized cache credit AND no reasoning activity in the range. A real
        # populated-zero (key present, no cache activity) — NOT the locked state.
        return self.total_rebate <= 0.0 and self.reasoning_total <= 0


def build_savings(rows: list) -> Savings:
    """Pure builder for #12 — the cache rebate + hit-rate + reasoning totals from
    QUERY A's SAME day rows (decision A: NO new query). Returns a Savings.

    - total_rebate = Σ abs(usage_cache) (decision B: negative=saving; abs handles
      an occasional positive 1-request day; only cache rows with cached_tokens>0
      or usage_cache!=0 contribute to the rebate sum).
    - hit_rate_pct = REQUEST-WEIGHTED mean of per-model cache_hit_rate ×100, in
      [0,100], guarded /0 (a row's weight is its request_count; hit_rate is a
      0..1 fraction multiplied by 100 here, ONCE).
    - reasoning_total = Σ reasoning_tokens (a COUNT).
    - per-model breakdown sorted by rebate desc; spark = daily abs(usage_cache).
    """
    rows = rows or []
    # Per-model roll-up + a per-day abs(usage_cache) series for the sparkline.
    bucket_order: list = []
    seen_buckets = set()
    per_model: dict = defaultdict(lambda: {
        "rebate": 0.0, "cached": 0, "reason": 0,
        # request-weighted hit-rate accumulators (Σ hit*reqs / Σ reqs).
        "hit_wsum": 0.0, "hit_wreq": 0,
    })
    day_rebate: dict = defaultdict(float)   # bucket -> Σ abs(usage_cache)
    day_reason: dict = defaultdict(int)     # bucket -> Σ reasoning_tokens

    # Global request-weighted hit-rate accumulators.
    g_hit_wsum = 0.0
    g_hit_wreq = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        bk = _bucket_key(row)
        bucket = row.get(bk) if bk else None
        model = row.get("model") or ""
        ucache = _as_float(row.get("usage_cache"))
        cached = _as_int(row.get("cached_tokens"))
        reason = _as_int(row.get("reasoning_tokens"))
        hit = _as_float(row.get("cache_hit_rate"))   # 0..1 fraction
        reqs = _as_int(row.get("request_count"))
        if bucket is not None and bucket not in seen_buckets:
            seen_buckets.add(bucket)
            bucket_order.append(bucket)

        # Only count cache ACTIVITY toward the rebate (decision B): a row with no
        # cached tokens AND a zero usage_cache contributes nothing.
        rebate = abs(ucache) if (cached > 0 or ucache != 0.0) else 0.0
        agg = per_model[model]
        agg["rebate"] += rebate
        agg["cached"] += cached
        agg["reason"] += reason
        # Request-weighted hit-rate: weight each row's fraction by its requests.
        if reqs > 0:
            agg["hit_wsum"] += hit * reqs
            agg["hit_wreq"] += reqs
            g_hit_wsum += hit * reqs
            g_hit_wreq += reqs
        if bucket is not None:
            day_rebate[bucket] += rebate
            day_reason[bucket] += reason

    bucket_order.sort()  # chronological (ISO date strings sort correctly)
    total_rebate = sum(v["rebate"] for v in per_model.values())
    reasoning_total = sum(v["reason"] for v in per_model.values())
    # ×100 ONCE, here at parse (decision B) -> a [0,100] percent, guarded /0.
    hit_rate_pct = (g_hit_wsum / g_hit_wreq * 100.0) if g_hit_wreq > 0 else 0.0
    hit_rate_pct = max(0.0, min(100.0, hit_rate_pct))

    # Per-model breakdown, heaviest rebate first (rhymes with #9/#10 ordering).
    ordered = sorted(per_model.keys(),
                     key=lambda m: (-per_model[m]["rebate"], m))
    models = tuple(
        SavingsModel(
            model_id=m,
            short_name=_short_model(m),
            rebate=per_model[m]["rebate"],
            cached_tokens=per_model[m]["cached"],
            hit_rate_pct=max(0.0, min(100.0, (
                per_model[m]["hit_wsum"] / per_model[m]["hit_wreq"] * 100.0
            ) if per_model[m]["hit_wreq"] > 0 else 0.0)),
            reasoning_tokens=per_model[m]["reason"],
        )
        for m in ordered
        # popup shows models that actually saved or reasoned (no $0/0-tok rows)
        if per_model[m]["rebate"] > 0.0 or per_model[m]["reason"] > 0
    )

    spark = tuple(day_rebate.get(b, 0.0) for b in bucket_order)
    # Meter normalize basis = max single-day reasoning count (guarded /0 in the
    # widget; 0 here means a tidy zero-height capsule, never a divide-by-zero).
    reasoning_ref = max((day_reason.get(b, 0) for b in bucket_order), default=0)

    return Savings(
        total_rebate=total_rebate,
        hit_rate_pct=hit_rate_pct,
        reasoning_total=reasoning_total,
        models=models,
        spark=spark,
        reasoning_ref=reasoning_ref,
    )


# --- #13 THE SÉANCE (ghost model detector) ----------------------------------
# Pair identity is (model, provider). The week-over-week diff cross-references
# two week-granularity dims=[model,provider] envelopes:
#   LIVING   = A ∩ B  (still in the room)
#   VANISHED = B − A  (departed; carries B's prior-week figures + last-seen)
#   APPEARED = A − B  (materialized this week; carries A's figures)
# YOUNG-HISTORY GUARD (decision A): if Window B has NO rows the prior week has
# no/insufficient data — a naive A−B would flag EVERY pair as a false apparition.
# So B-empty -> young_history=True, the diff is SUPPRESSED (vanished/appeared
# empty, living = A's pairs), and the widget paints the calm "watching" state.

@dataclass(frozen=True)
class GhostPair:
    """One (model, provider) pair's presence figures for a single window. The
    SÉANCE ledger reads these for the two-bar mini-timeline. request_count is a
    COUNT (_as_int over the STRING); usage is total_usage ($)."""
    model_id: str
    provider: str
    short_name: str
    request_count: int
    usage: float
    bucket: str = ""          # the created_at__week bucket date the API returned

    @property
    def key(self) -> tuple:
        return (self.model_id, self.provider)


@dataclass(frozen=True)
class GhostEntry:
    """A living/vanished/appeared roster entry — a pair plus the cross-window
    context the widget + ledger need. `rank` is the model's descending-spend
    rank across BOTH windows so the chip color == that model's #9 spectrum band
    (the shared spend_palette.model_color contract — decision D). `reroute` flags
    a benign 'same model, new provider' move (decision C): set on the appeared/
    vanished entries when the model survives but only its provider half changed.
    `this` / `prior` carry the A / B figures for the ledger (either may be None
    for a vanished/appeared pair)."""
    pair: GhostPair                 # the canonical pair (the surviving-window one)
    rank: int                       # model's descending-spend rank (shared color)
    this: Optional[GhostPair] = None    # Window A figures (None if vanished)
    prior: Optional[GhostPair] = None   # Window B figures (None if appeared)
    reroute: bool = False           # same model, new/old provider (benign)


@dataclass(frozen=True)
class GhostDiff:
    """#13 THE SÉANCE payload — the week-over-week (model,provider) roster diff.

    living/vanished/appeared are tuple[GhostEntry]. young_history=True means the
    prior week had no data (the live state on this young account) -> the widget
    renders the calm "watching — needs a 2nd full week" state and the diff is
    suppressed (no phantom apparitions). ranges carries the two window labels for
    the ledger; week_bucket_a/b are the API's returned created_at__week bucket
    dates the windows aligned to (decision B — NOT a client-side calendar week)."""
    living: tuple = ()
    vanished: tuple = ()
    appeared: tuple = ()
    young_history: bool = False
    range_a: str = ""               # this-week window label
    range_b: str = ""               # prior-week window label
    week_bucket_a: str = ""         # the API's this-week bucket date
    week_bucket_b: str = ""         # the API's prior-week bucket date

    @property
    def is_locked_placeholder(self) -> bool:
        return False

    @property
    def has_ghosts(self) -> bool:
        return bool(self.vanished or self.appeared)

    def living_pairs(self) -> tuple:
        return tuple(e.pair.key for e in self.living)

    def vanished_pairs(self) -> tuple:
        return tuple(e.pair.key for e in self.vanished)

    def appeared_pairs(self) -> tuple:
        return tuple(e.pair.key for e in self.appeared)


def _ghost_pairs(rows: list, only_bucket: Optional[str] = None) -> tuple:
    """Roll week rows up to ({(model,provider): GhostPair}, {bucket_dates}).
    When `only_bucket` is given, ONLY rows whose created_at__week equals it are
    aggregated (so we can split one envelope into its distinct week buckets —
    decision B aligns windows to the API's RETURNED bucket dates). request_count
    is a STRING -> _as_int. Multiple rows for one pair in a bucket sum."""
    rows = rows or []
    agg: dict = {}
    buckets_seen: set = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        bk = _bucket_key(row)
        bucket = (row.get(bk) if bk else "") or ""
        if bucket:
            buckets_seen.add(bucket)
        if only_bucket is not None and bucket != only_bucket:
            continue
        model = row.get("model") or ""
        provider = row.get("provider") or ""
        key = (model, provider)
        reqs = _as_int(row.get("request_count"))   # STRING -> int
        usage = _as_float(row.get("total_usage"))
        prev = agg.get(key)
        if prev is None:
            agg[key] = GhostPair(
                model_id=model, provider=provider,
                short_name=_short_model(model),
                request_count=reqs, usage=usage, bucket=bucket,
            )
        else:
            agg[key] = GhostPair(
                model_id=model, provider=provider,
                short_name=_short_model(model),
                request_count=prev.request_count + reqs,
                usage=prev.usage + usage,
                bucket=prev.bucket or bucket,
            )
    return agg, buckets_seen


def parse_ghost_diff(envelope_a, envelope_b=None, range_a: str = "",
                     range_b: str = "") -> GhostDiff:
    """Pure week-over-week (model,provider) diff (decisions A/B/C/D). Never raises.

    Takes two PARSED week envelopes ({"rows",...}) OR raw row lists. CRITICAL
    LIVE TRUTH (re-verified 2026-06-24): the OpenRouter `week` query IGNORES the
    date_range — every window returns the SAME set of created_at__week buckets
    (one bucket per week that has data). So we do NOT trust the client-side
    A/B windows; instead we POOL both envelopes' rows and key off the DISTINCT
    created_at__week bucket DATES the API actually returned (decision B):

      - latest bucket  -> Window A (this week)
      - 2nd-latest     -> Window B (prior week)

    YOUNG-HISTORY GUARD (decision A): if FEWER THAN 2 distinct week buckets exist
    (the live state on this account, which has exactly one week of data) there is
    no prior week to compare -> young_history=True, the diff is SUPPRESSED
    (living = the single week's pairs, appeared/vanished empty). This is what
    stops a naive A−B from flagging EVERY pair as a phantom apparition.

    With ≥2 distinct buckets: LIVING=A∩B, VANISHED=B−A (carry B figures),
    APPEARED=A−B (carry A). PAIR IDENTITY=(model,provider); a 'same model, new
    provider' move sets reroute=True on the appeared+vanished entries (decision C,
    a benign re-route). rank = the model's descending-spend rank across BOTH
    windows so a chip's color == that model's #9 band (decision D).
    """
    def _rows(env):
        if isinstance(env, dict):
            return env.get("rows") or []
        return env or []

    # Pool every row from both envelopes; the API hands the same buckets to each
    # window anyway, so a union is the honest source of "which weeks have data".
    all_rows = _rows(envelope_a) + _rows(envelope_b)
    _, all_buckets = _ghost_pairs(all_rows)
    # Distinct week buckets, NEWEST first (ISO dates sort lexicographically).
    distinct = sorted((b for b in all_buckets if b), reverse=True)

    # Shared descending-spend RANK per model across ALL data (the color key).
    pairs_all, _ = _ghost_pairs(all_rows)
    model_usage: dict = defaultdict(float)
    for p in pairs_all.values():
        model_usage[p.model_id] += p.usage
    ordered_models = sorted(model_usage.keys(),
                            key=lambda m: (-model_usage[m], m))
    rank_of = {m: i for i, m in enumerate(ordered_models)}

    def _entry(pair, this=None, prior=None, reroute=False):
        return GhostEntry(pair=pair, rank=rank_of.get(pair.model_id, 0),
                          this=this, prior=prior, reroute=reroute)

    def _sorter(keys):
        return sorted(keys, key=lambda k: (rank_of.get(k[0], 0), k))

    # ---- YOUNG-HISTORY GUARD: <2 distinct weeks -> NO diff (decision A) ------
    if len(distinct) < 2:
        bucket_a = distinct[0] if distinct else ""
        # Living = the single (latest) week's pairs; appeared/vanished SUPPRESSED
        # so we can't fabricate an apparition for every pair.
        living_pairs, _ = _ghost_pairs(all_rows, only_bucket=bucket_a) if bucket_a \
            else ({}, set())
        living = tuple(
            _entry(living_pairs[k], this=living_pairs[k])
            for k in _sorter(living_pairs.keys())
        )
        return GhostDiff(living=living, vanished=(), appeared=(),
                         young_history=True, range_a=range_a, range_b=range_b,
                         week_bucket_a=bucket_a, week_bucket_b="")

    # ---- ≥2 distinct weeks -> a real diff (latest vs 2nd-latest bucket) ------
    bucket_a, bucket_b = distinct[0], distinct[1]
    pairs_a, _ = _ghost_pairs(all_rows, only_bucket=bucket_a)
    pairs_b, _ = _ghost_pairs(all_rows, only_bucket=bucket_b)

    models_a = {p.model_id for p in pairs_a.values()}
    models_b = {p.model_id for p in pairs_b.values()}

    keys_a = set(pairs_a)
    keys_b = set(pairs_b)
    living_keys = keys_a & keys_b
    vanished_keys = keys_b - keys_a
    appeared_keys = keys_a - keys_b

    living = tuple(
        _entry(pairs_a[k], this=pairs_a[k], prior=pairs_b.get(k))
        for k in _sorter(living_keys)
    )
    vanished = tuple(
        _entry(pairs_b[k], prior=pairs_b[k],
               # reroute: this model still exists in A under a DIFFERENT provider.
               reroute=(k[0] in models_a))
        for k in _sorter(vanished_keys)
    )
    appeared = tuple(
        _entry(pairs_a[k], this=pairs_a[k],
               # reroute: this model was already in B under a DIFFERENT provider.
               reroute=(k[0] in models_b))
        for k in _sorter(appeared_keys)
    )
    return GhostDiff(living=living, vanished=vanished, appeared=appeared,
                     young_history=False, range_a=range_a, range_b=range_b,
                     week_bucket_a=bucket_a, week_bucket_b=bucket_b)


# Alias to match the F3 build_* naming the design references.
build_ghost_diff = parse_ghost_diff


def build_spend_board(rows: list, granularity: str = "day", start: str = "",
                      end: str = "", range_label: str = "Last 7 Days",
                      truncated: bool = False, ghosts=None) -> SpendBoard:
    """Build the aggregate SpendBoard from QUERY A's rows. #9's .spectrum,
    #10's .receipts AND #12's .savings are all populated from the SAME rows (no
    new query). #13's .ghosts is passed in (it rides its OWN two week queries,
    fired in get_spend_board) so this stays a pure function of QUERY A's rows for
    the existing tests; later features' slots stay empty until they ride this
    same cached envelope."""
    spectrum = build_spend_spectrum(rows, granularity=granularity,
                                    truncated=truncated)
    receipts = build_receipts(rows)
    savings = build_savings(rows)
    return SpendBoard(spectrum=spectrum, receipts=receipts, savings=savings,
                      ghosts=ghosts, start=start, end=end,
                      range_label=range_label)


# --- #14 THE HOURGLASS (budget burn-down) ----------------------------------
# The sand-clock races the calendar: the BOTTOM bulb is spend (drained sand),
# the TOP bulb is the remaining budget, and a diagonal PACE tick marks where the
# sand SHOULD be given % of the period elapsed. The pinch reddens when spend is
# AHEAD OF PACE (before 100%). NEVER invent a denominator — it comes only from
# settings.weekly_budget (>0) or the opt-in credits fallback (decision A).

# Per-bulb height in px (the spec's GEOMETRY_PLAN: each bulb is 28px tall). The
# PURE geometry helper works in these px so it's unit-testable WITHOUT Qt.
HOURGLASS_BULB_H = 28.0


def budget_geometry(spent: float, budget: float, elapsed_frac: float):
    """PURE (no Qt) hourglass geometry (decision C). Returns
    (top_h, bottom_h, pace_y, over_pace):

      - spent_frac = clamp(spent/budget, 0, 1), guarded for budget<=0 (-> 0.0).
      - bottom_h = BULB_H * spent_frac        (spend GROWS the bottom bulb)
      - top_h    = BULB_H * (1 - spent_frac)  (remaining SHRINKS the top bulb)
      - pace_y   = BULB_H * clamp(elapsed_frac,0,1)  — the height into the bottom
                   'should-spent' bulb where the sand OUGHT to be by now.
      - over_pace = spent_frac > elapsed_frac (the single 'in trouble' signal;
                    RED is scoped strictly to this so it doesn't fight #11/#13).

    Inversion (spent->bottom, remaining->top) and the pace mapping are the whole
    point of the unit test; the widget just scales these into its measured glass.
    """
    ef = max(0.0, min(1.0, float(elapsed_frac)))
    if budget is None or budget <= 0:
        spent_frac = 0.0
    else:
        spent_frac = max(0.0, min(1.0, float(spent) / float(budget)))
    bottom_h = HOURGLASS_BULB_H * spent_frac
    top_h = HOURGLASS_BULB_H * (1.0 - spent_frac)
    pace_y = HOURGLASS_BULB_H * ef
    over_pace = spent_frac > ef
    return top_h, bottom_h, pace_y, over_pace


@dataclass(frozen=True)
class Budget:
    """#14 payload — the budget burn-down state (decision E: three distinct
    states, ZERO fabricated numbers).

    source:
      "weekly"  -> settings.weekly_budget (>0), the real config path.
      "credits" -> the opt-in credits fallback (budget=total_credits,
                   burned=total_usage; the only live spend-cap signal).
      "none"    -> NO budget configured -> the dashed "Set a budget" state.
      "locked"  -> no management key (set by the widget's set_locked()).

    For "none"/"locked" the numeric fields are 0 and the widget paints the
    no-budget / padlocked glass WITHOUT inventing a denominator.
    spent_frac/elapsed_frac are [0,1]; over_pace == spent_frac > elapsed_frac.
    daily is the per-day spend series ((label,$),...) for the popup column chart.
    """
    spent: float = 0.0
    budget: float = 0.0
    spent_frac: float = 0.0
    elapsed_frac: float = 0.0
    days_left: int = 0
    elapsed_days: int = 0
    projection: float = 0.0
    avg_daily: float = 0.0
    over_pace: bool = False
    source: str = "none"
    period_days: int = 7
    daily: tuple = ()        # tuple[(bucket_label, usage)] chronological

    @property
    def has_budget(self) -> bool:
        return self.source in ("weekly", "credits") and self.budget > 0

    @property
    def pct_burned(self) -> int:
        return int(round(self.spent_frac * 100.0))

    @property
    def over_projection(self) -> bool:
        """The projection row goes RED when the forecast overshoots the budget."""
        return self.has_budget and self.projection > self.budget


def build_budget(rows: list, budget_value: float, period_start, now,
                 source: str = "weekly", period_days: int = 7,
                 credits_spent: Optional[float] = None) -> Budget:
    """PURE builder for #14 (decision D). Sums QUERY D's day rows (total_usage,
    dims=[], granularity=day) into period-to-date spend + a daily series, and
    computes the projection. NEVER raises; NEVER fabricates a denominator.

    - spent = Σ total_usage across the rows (a real float; no coercion needed,
      but _as_float is defensive). For the credits source the caller passes
      credits_spent (total_usage from /credits) so spent == the live burned $.
    - elapsed_frac = elapsed_days / period_days, where elapsed_days counts the
      DISTINCT day buckets the API returned (aligned to the same fetch — decision
      D), guarded so elapsed_days==0 -> elapsed_frac 0 and avg_daily 0 (the young
      account). days_left = max(0, period_days - elapsed_days).
    - avg_daily = spent / elapsed_days (guard /0); projection = spent +
      avg_daily * days_left.
    - over_pace = spent_frac > elapsed_frac (RED pinch, decision C).

    source != "weekly"/"credits" (i.e. no denominator) -> a Budget("none") with
    zeroed numerics so the widget paints "Set a budget" (decision A/E).
    """
    rows = rows or []
    # Build the chronological daily series + Σ from QUERY D's rows.
    day_usage: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        bk = _bucket_key(row)
        bucket = row.get(bk) if bk else None
        if bucket is None:
            continue
        day_usage[bucket] = day_usage.get(bucket, 0.0) + _as_float(
            row.get("total_usage"))
    buckets = sorted(day_usage.keys())   # ISO date strings sort chronologically
    daily = tuple((b, day_usage[b]) for b in buckets)
    summed = sum(day_usage.values())

    # No denominator -> the honest "Set a budget" state (decision A).
    if source not in ("weekly", "credits") or budget_value is None \
            or budget_value <= 0:
        return Budget(spent=summed, budget=0.0, source="none",
                      period_days=period_days, daily=daily,
                      elapsed_days=len(buckets))

    budget = float(budget_value)
    # For the credits fallback, the live "burned" figure is the authoritative
    # spend (total_usage vs total_credits); the day rows still feed the series.
    spent = float(credits_spent) if (source == "credits"
                                     and credits_spent is not None) else summed

    # elapsed_days = the DISTINCT day buckets actually returned (decision D),
    # capped to the period so a wider fetch can't push elapsed_frac past 1.
    elapsed_days = min(len(buckets), period_days) if buckets else 0
    days_left = max(0, period_days - elapsed_days)
    elapsed_frac = (elapsed_days / period_days) if period_days > 0 else 0.0
    elapsed_frac = max(0.0, min(1.0, elapsed_frac))
    avg_daily = (spent / elapsed_days) if elapsed_days > 0 else 0.0   # guard /0
    projection = spent + avg_daily * days_left
    spent_frac = max(0.0, min(1.0, (spent / budget) if budget > 0 else 0.0))
    over_pace = spent_frac > elapsed_frac

    return Budget(
        spent=spent, budget=budget, spent_frac=spent_frac,
        elapsed_frac=elapsed_frac, days_left=days_left, elapsed_days=elapsed_days,
        projection=projection, avg_daily=avg_daily, over_pace=over_pace,
        source=source, period_days=period_days, daily=daily,
    )


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
        self._meta = None  # lazy; optional granularity/dimension validation

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

    def get_providers(self) -> list:
        try:
            resp = self.session.get(PROVIDERS_ENDPOINT, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [
                ProviderInfo(
                    name=p.get("name", ""),
                    slug=p.get("slug", ""),
                    status_page_url=p.get("status_page_url"),
                    headquarters=p.get("headquarters"),
                )
                for p in data
            ]
        except Exception as e:
            log.warning("providers fetch failed: %s", e)
            return []

    def get_models(self) -> list:
        try:
            resp = self.session.get(MODELS_ENDPOINT, timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            models = []
            for m in data:
                pricing = m.get("pricing", {})
                prompt_price = pricing.get("prompt", "0")
                completion_price = pricing.get("completion", "0")
                try:
                    pp = float(prompt_price)
                except (ValueError, TypeError):
                    pp = 0.0
                try:
                    cp = float(completion_price)
                except (ValueError, TypeError):
                    cp = 0.0
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

    def get_model_count(self) -> int:
        try:
            resp = self.session.get(MODELS_COUNT_ENDPOINT, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("count", data.get("data", {}).get("count", 0))
        except Exception as e:
            log.warning("model count fetch failed: %s", e)
            return 0

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

    def get_service_status(self) -> ServiceStatus:
        try:
            resp = self.session.get(STATUS_URL, timeout=10)
            text = resp.text.lower()
            status = ServiceStatus()
            if "all systems operational" in text:
                status.overall = "operational"
                status.chat_api = "operational"
                status.data_api = "operational"
                status.homepage = "operational"
            elif "operational" in text:
                status.overall = "degraded"
                status.chat_api = "operational"
                status.data_api = "operational"
                status.homepage = "operational"
            else:
                status.overall = "degraded"
                status.chat_api = "degraded"
                status.data_api = "degraded"
                status.homepage = "degraded"
            return status
        except Exception as e:
            log.warning("status fetch failed: %s", e)
            return ServiceStatus()

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


class APIWorker(QObject):
    """Background worker that fetches data and emits signals."""
    key_info_ready = Signal(object)
    models_ready = Signal(object)
    model_count_ready = Signal(int)
    status_ready = Signal(object)
    providers_ready = Signal(object)
    endpoints_ready = Signal(str, object)   # (model_id, ModelEndpoints|None)
    benchmarks_ready = Signal(object)       # BenchmarkBoard | None
    provider_trust_ready = Signal(object)   # ProviderTrustBook | None  (no-auth)
    speed_board_ready = Signal(object)      # SpeedBoard | None  (no-auth, #4)
    trend_ready = Signal(object)            # TrendBoard | None  (no-auth, #7 THE TAPE)
    permaslug_resolver_ready = Signal(object)  # PermaslugResolver | None (no-auth)
    uptime_ready = Signal(str, object)      # (model_id, {ep_ident: UptimeHistory}) (no-auth, #3)
    spend_ready = Signal(object)            # SpendBoard | None (mgmt-key analytics, Wave 2 F3/#9)
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
    def fetch_model_count(self):
        try:
            count = self.client.get_model_count()
            self.model_count_ready.emit(count)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def fetch_status(self):
        try:
            status = self.client.get_service_status()
            self.status_ready.emit(status)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def fetch_providers(self):
        try:
            providers = self.client.get_providers()
            self.providers_ready.emit(providers)
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