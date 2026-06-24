"""
OpenRouter Pulse - API Client
Handles all communication with OpenRouter API endpoints.
"""
import logging
import re
import requests
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from PySide6.QtCore import QObject, Signal, Slot, QThread

from config import (
    API_KEY, API_KEY_ENDPOINT, MODELS_ENDPOINT,
    MODELS_COUNT_ENDPOINT, STATUS_URL,
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
    permaslug_resolver_ready = Signal(object)  # PermaslugResolver | None (no-auth)
    uptime_ready = Signal(str, object)      # (model_id, {ep_ident: UptimeHistory}) (no-auth, #3)
    logo_ready = Signal(str, object, bool)  # (slug, raw_bytes|None, is_svg)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.client = APIClient()
        # The no-auth frontend client (foundation F2) rides on the same worker
        # thread; it carries its own session (no key, browser-ish UA).
        from frontend_client import FrontendClient
        self.frontend = FrontendClient()

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