"""#16 THE TITLE BELT — the pure week-champion compute layer (no Qt, no I/O).

The Insights zone's SECOND widget (under #15): this week's top-SPEND model,
engraved on a championship belt. The compute is a pure roll-up over the weekly
``dims=[model]`` analytics rows the worker fetches (``fetch_insights``), so it is
unit-testable with zero Qt and reusable by #18 from the same cached envelope.

Dependency-light on purpose (mirrors ``value_assay`` / ``spend_palette``'s
no-QWidget discipline) so it imports cleanly into dashboard.py, widgets.py, AND
the tests without a cycle. The render widget (``ModelOfWeekBelt``, widgets.py)
and the wiring (``APIWorker.fetch_insights`` → ``InsightsBoard.week``) both
consume ``ModelOfWeek`` / ``build_model_of_week``.

YOUNG-ACCOUNT HONESTY (the live state today): the real data has exactly ONE week
bucket (``date__week=2026-06-21``), so a week-over-week share/rank shift is
UNDEFINED — ``wow_delta`` stays ``None`` and the widget paints a muted
"WEEK 1 · NO PRIOR ROUND" ribbon, NEVER a fabricated delta. A signed delta is
produced ONLY once a second distinct week bucket exists (decision B).

Quirks respected (decision C): the time-bucket key is detected via the analytics
``_bucket_key`` regex (NEVER hardcoded — works for ``date__week`` today and a
hypothetical ``created_at__week``); ``total_usage`` is a JSON number
(``_as_float``); ``tokens_total`` / ``request_count`` are JSON STRINGS
(``_as_int``). The share denominator is the sum of ``total_usage`` WITHIN the
latest bucket only (100% today), never across the whole 21-day range.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional

# Imported lazily-safe from the analytics module (pure helpers, no Qt, no I/O).
# Kept as a module-level import because api_client itself imports no Qt at this
# layer and these three are tiny pure functions (regex + numeric coercion).
from api_client import _bucket_key, _as_float, _as_int


@dataclass(frozen=True)
class ModelOfWeek:
    """The widget payload for THE TITLE BELT — the latest week's champion plus
    the honest comparison state.

    champion_id    : full model id (e.g. 'anthropic/claude-4.6-sonnet-20260217').
    champion_name  : a humanized display name for the escutcheon engraving.
    provider       : the model-id provider prefix ('anthropic') — drives the logo
                     lookup / monogram fallback (decision D).
    share          : champ_usage / Σ(usage WITHIN the latest bucket) in 0..1.
    week_spend     : the champion's total_usage this week ($, float).
    week_tokens    : the champion's tokens_total this week (int; coerced STRING).
    week_requests  : the champion's request_count this week (int; coerced STRING).
    bucket_label   : the latest bucket key value ('2026-06-21').
    date_label     : a human week label ('Week of Jun 21 2026').
    week_count     : the number of DISTINCT week buckets in the range.
    wow_delta      : signed change in the champion's share vs the prior week
                     (fraction, e.g. +0.18), or None when <2 buckets (Week-1).
    wow_rank_delta : signed change in the champion's rank vs the prior week
                     (e.g. +1 == climbed one place), or None when <2 buckets.
    runner_up_id   : the 2nd-place model id this week (for the dossier trace), or ''.
    runner_up_name : the 2nd-place humanized name, or ''.
    runner_up_spend: the 2nd-place spend this week ($), or 0.0.
    """
    champion_id: str = ""
    champion_name: str = ""
    provider: str = ""
    share: float = 0.0
    week_spend: float = 0.0
    week_tokens: int = 0
    week_requests: int = 0
    bucket_label: str = ""
    date_label: str = ""
    week_count: int = 0
    wow_delta: Optional[float] = None
    wow_rank_delta: Optional[int] = None
    runner_up_id: str = ""
    runner_up_name: str = ""
    runner_up_spend: float = 0.0

    @property
    def is_empty(self) -> bool:
        """No spend booked this week -> the tidy 'No spend yet this week' belt."""
        return self.week_count == 0 or not self.champion_id

    @property
    def is_week_one(self) -> bool:
        """The young-account honest state: a champion exists but there is no
        prior week to compare against (the 'WEEK 1 · NO PRIOR ROUND' ribbon,
        NEVER a fabricated delta)."""
        return (not self.is_empty) and self.wow_delta is None

    @property
    def share_pct(self) -> float:
        return self.share * 100.0


def provider_of(model_id: str) -> str:
    """The provider prefix of a model id ('anthropic/claude-4.6-sonnet' ->
    'anthropic'). '' when there's no prefix. Drives the logo-store slug lookup
    and the monogram-disc fallback (decision D)."""
    if not model_id:
        return ""
    return model_id.split("/", 1)[0] if "/" in model_id else ""


def humanize_model(model_id: str) -> str:
    """A compact display name from a raw analytics model id.

    'anthropic/claude-4.6-sonnet-20260217' -> 'Claude 4.6 Sonnet'
    'anthropic/claude-4.5-haiku'            -> 'Claude 4.5 Haiku'

    Strips the vendor/ prefix and a trailing -YYYYMMDD date stamp, splits on '-',
    and title-cases each token (leaving version tokens like '4.6' intact). Pure;
    the widget elides to fit and html.escape guards the dossier wrapper."""
    if not model_id:
        return ""
    stem = model_id.split("/", 1)[1] if "/" in model_id else model_id
    parts = stem.split("-")
    # Drop a trailing all-digit date/version stamp (e.g. '20260217').
    if len(parts) > 1 and parts[-1].isdigit() and len(parts[-1]) >= 6:
        parts = parts[:-1]
    out = []
    for tok in parts:
        if not tok:
            continue
        # Keep tokens that contain a digit verbatim (e.g. '4.6', 'v4', '5.2');
        # title-case pure-alpha words.
        out.append(tok if any(ch.isdigit() for ch in tok) else tok.capitalize())
    return " ".join(out) or stem


def _week_date_label(bucket_label: str) -> str:
    """'2026-06-21' -> 'Week of Jun 21 2026'. Falls back to the raw label if the
    bucket value isn't an ISO date (defensive — the key is regex-detected, the
    value is whatever the API returned)."""
    if not bucket_label:
        return ""
    try:
        d = datetime.date.fromisoformat(bucket_label[:10])
        return d.strftime("Week of %b %d %Y")
    except (ValueError, TypeError):
        return bucket_label


def _bucket_models(rows: list, bucket_key: Optional[str]):
    """Group rows by their bucket value -> {bucket_value: [rows]} (insertion via
    a plain dict so iteration is deterministic). Rows missing the key bucket
    under None so they aren't silently dropped from the count."""
    buckets: dict = {}
    for row in rows:
        key = row.get(bucket_key) if bucket_key else None
        buckets.setdefault(key, []).append(row)
    return buckets


def build_model_of_week(rows: list) -> ModelOfWeek:
    """Pure builder: the latest week's champion + the honest WoW state.

    rows: the parsed analytics rows from the weekly dims=[model] query
          (metrics ['total_usage','tokens_total','request_count']).

    Contract (decisions B/C):
      - bucket key detected via _bucket_key (NEVER hardcoded date__week).
      - champion = max total_usage in the LATEST bucket; share = champ / Σ(usage
        WITHIN that latest bucket only) — NOT across the whole range.
      - total_usage via _as_float; tokens_total / request_count via _as_int
        (they arrive as STRINGS).
      - week_count = number of DISTINCT week buckets.
      - wow_delta / wow_rank_delta are signed (latest-vs-prior) ONLY when
        week_count >= 2; with <2 buckets they stay None (the Week-1 ribbon).
      - zero buckets / zero champion -> is_empty ('No spend yet this week').
    Never raises.
    """
    rows = rows or []
    if not rows:
        return ModelOfWeek(week_count=0)

    bucket_key = _bucket_key(rows[0])
    buckets = _bucket_models(rows, bucket_key)
    # Sort bucket values; None (missing key) sorts last so a real date is latest.
    ordered_keys = sorted(buckets.keys(), key=lambda k: (k is None, k or ""))
    week_count = len(ordered_keys)
    latest_key = ordered_keys[-1] if ordered_keys else None
    latest_rows = buckets.get(latest_key, [])

    def _ranked(bucket_rows):
        # Descending total_usage; model id as a stable deterministic tiebreaker.
        return sorted(
            bucket_rows,
            key=lambda r: (-_as_float(r.get("total_usage")), str(r.get("model") or "")),
        )

    ranked = _ranked(latest_rows)
    if not ranked:
        return ModelOfWeek(week_count=week_count,
                           bucket_label=(latest_key or ""),
                           date_label=_week_date_label(latest_key or ""))

    champ = ranked[0]
    champ_id = str(champ.get("model") or "")
    champ_usage = _as_float(champ.get("total_usage"))
    bucket_sum = sum(_as_float(r.get("total_usage")) for r in latest_rows)
    share = (champ_usage / bucket_sum) if bucket_sum > 0 else 0.0

    runner = ranked[1] if len(ranked) >= 2 else None
    runner_id = str(runner.get("model") or "") if runner is not None else ""
    runner_spend = _as_float(runner.get("total_usage")) if runner is not None else 0.0

    # WoW: only when a prior distinct bucket exists (decision B — never faked).
    wow_delta: Optional[float] = None
    wow_rank_delta: Optional[int] = None
    if week_count >= 2:
        prior_key = ordered_keys[-2]
        prior_rows = buckets.get(prior_key, [])
        prior_sum = sum(_as_float(r.get("total_usage")) for r in prior_rows)
        prior_ranked = _ranked(prior_rows)
        # The champion's share in the prior week (0 if it wasn't present then).
        prior_share = 0.0
        prior_rank = None
        for i, r in enumerate(prior_ranked):
            if str(r.get("model") or "") == champ_id:
                pu = _as_float(r.get("total_usage"))
                prior_share = (pu / prior_sum) if prior_sum > 0 else 0.0
                prior_rank = i
                break
        wow_delta = share - prior_share
        if prior_rank is not None:
            # Positive == climbed (prior position number was larger/worse).
            wow_rank_delta = prior_rank - 0  # champion is rank 0 this week
        else:
            wow_rank_delta = None  # new entrant — no prior rank to diff

    return ModelOfWeek(
        champion_id=champ_id,
        champion_name=humanize_model(champ_id),
        provider=provider_of(champ_id),
        share=share,
        week_spend=champ_usage,
        week_tokens=_as_int(champ.get("tokens_total")),
        week_requests=_as_int(champ.get("request_count")),
        bucket_label=(latest_key or ""),
        date_label=_week_date_label(latest_key or ""),
        week_count=week_count,
        wow_delta=wow_delta,
        wow_rank_delta=wow_rank_delta,
        runner_up_id=runner_id,
        runner_up_name=humanize_model(runner_id),
        runner_up_spend=runner_spend,
    )
