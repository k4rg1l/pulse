"""Pure Spend / Insights business logic (F3 — analytics ground-truth spend).

Extracted from api_client.py: dependency-light (no Qt, no HTTP), unit-tested
against captured analytics rows. AnalyticsClient/APIWorker in api_client.py call
these builders and RE-EXPORT the public names, so ``from api_client import
build_spend_board`` (etc.) keeps working.

Analytics is management-key-gated (the whole Spend zone rides F3). The day/hour/
week bucket-key NAME is not stable across dimension sets, so the parsers detect
it by regex -- never hardcode ``date__day``.
"""

import datetime
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from num import as_float as _as_float, as_int as _as_int


# The day/hour/week bucket key NAME is NOT stable across dimension sets:
#   dims=[] or [model]      -> date__day / date__hour / date__week
#   dims=[model, provider]  -> created_at__day / created_at__hour / ...
# So NEVER hardcode date__day — detect the single key matching this regex.
_BUCKET_KEY_RE = re.compile(r"^(date|created_at)__(minute|hour|day|week|month)$")


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


@dataclass(frozen=True)
class InsightsBoard:
    """Wave 3 INSIGHTS zone aggregate — the sibling of SpendBoard for the
    Insights widgets. fetch_insights returns ONE of these (or None) and the
    dashboard fans each slot to its widget. Every slot is independently None-able
    so a partial mgmt-query failure degrades per-widget (the zone never blanks).

    SCOPE NOTE (Wave 3 scaffold): #15 THE ASSAY rides `_distribute_value()` off
    already-fetched USER-key stores, so `.value` is INDEPENDENT of this board (it
    is never the source for #15). The other slots are reserved for the later mgmt
    features and stay None until #16/#17/#18 add their analytics queries:
      .week     -> #16 Model of the Week (weekly dims=[model])
      .recorder -> #17 Token Recorder (daily dims=[] full-range)
      .court    -> #18 Task Court & Climb (classifications/task + rankings/apps)
    """
    value: Optional[object] = None        # #15 (rides _distribute_value; board slot reserved)
    week: Optional[object] = None         # #16 (mgmt) — None until built
    recorder: Optional[object] = None     # #17 (mgmt) — None until built
    court: Optional[object] = None        # #18 (mixed) — None until built


def _day_axis(start: str, end: str):
    """Every ISO day from start..end inclusive (the zero-fill axis), or None
    when the range doesn't parse or is silly-long. Accepts full ISO datetimes
    (the fetch passes datetimes; the day buckets are bare dates)."""
    try:
        d0 = datetime.date.fromisoformat((start or "")[:10])
        d1 = datetime.date.fromisoformat((end or "")[:10])
    except ValueError:
        return None
    if d1 < d0 or (d1 - d0).days > 40:
        return None
    return [(d0 + datetime.timedelta(days=i)).isoformat()
            for i in range((d1 - d0).days + 1)]


def build_spend_spectrum(rows: list, granularity: str = "day",
                         truncated: bool = False, start: str = "",
                         end: str = "") -> SpendSpectrumData:
    """Pure builder: the per-bucket per-model matrix + descending-spend model
    roll-up + hero total + spike bucket. Honors the data quirks:
      - bucket key detected via _bucket_key (date__day OR created_at__*)
      - request_count/tokens via _as_int (STRINGS); total_usage via _as_float
      - divide-by-zero guarded (share 0 when total==0)
      - the API returns only days WITH usage; when start/end parse, the day
        axis is zero-filled across the whole range so a one-day-old account
        draws a spike on that day, not one giant full-chart block
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

    # Zero-fill the day axis across the queried range (only when there IS
    # data: an all-empty range keeps buckets=() -> the tidy empty state).
    if granularity == "day" and rows and bucket_order:
        axis = _day_axis(start, end)
        if axis:
            for b in axis:
                if b not in seen_buckets:
                    seen_buckets.add(b)
                    bucket_order.append(b)

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


def _parse_iso_utc(s):
    """Tolerant ISO parse -> naive UTC datetime, or None. Accepts 'Z', offsets,
    a space separator, and bare dates (midnight)."""
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def build_autopsy(rows: list, t0_iso: str, t1_iso: str,
                  label: Optional[str] = None) -> AutopsyReport:
    """Pure builder for #11 — aggregate the hourly dims=[model,provider] rows of
    the lassoed window into per-(model,provider) incisions, descending by $.

    - rows are CLAMPED to [t0, t1] by their bucket timestamp. Caught live
      2026-07-01: the analytics API returned rows OUTSIDE the requested window
      (lassoing a zero-spend flat zone showed today's spend), so the endpoint's
      clamping cannot be trusted. A row with an unparseable/absent bucket gets
      the benefit of the doubt; the end bound is inclusive because the client
      passes an already-expanded exclusive end while raw bucket labels (the
      tests' convention) name their bucket inclusively.
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

    w0 = _parse_iso_utc(t0_iso)
    w1 = _parse_iso_utc(t1_iso)
    if w0 is not None and w1 is not None and w1 < w0:
        w0, w1 = w1, w0
    clamp = w0 is not None and w1 is not None

    for row in rows:
        if not isinstance(row, dict):
            continue
        if clamp:
            bk = _bucket_key(row)
            bdt = _parse_iso_utc(row.get(bk)) if bk else None
            if bdt is not None and not (w0 <= bdt <= w1):
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
    def has_ghosts(self) -> bool:
        return bool(self.vanished or self.appeared)


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
                                    truncated=truncated, start=start, end=end)
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
