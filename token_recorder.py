"""#17 THE FLIGHT RECORDER — the pure odometer/records/streak compute layer
(no Qt, no I/O).

The Insights zone's THIRD widget (under #16): your LIFETIME token odometer, your
single record "black-box" day, and your active-day RUNWAY streak — three thin
facts fused into one short-but-real flight log. The compute is a pure roll-up
over the daily ``dims=[]`` full-range analytics rows the worker fetches
(``fetch_insights``), so it is unit-testable with zero Qt and the same ``dims=[]``
total is reused by #18 from the same cached envelope.

Dependency-light on purpose (mirrors ``model_of_week`` / ``value_assay`` /
``spend_palette``'s no-QWidget discipline) so it imports cleanly into
dashboard.py, widgets.py, AND the tests without a cycle. The render widget
(``TokenRecorder``, widgets.py) and the wiring
(``APIWorker.fetch_insights`` → ``InsightsBoard.recorder``) both consume
``TokenRecord`` / ``build_token_recorder``.

CRITICAL zero-day handling (decision B): the daily query returns ONLY active days
(3 rows over the 60-day window today) — gaps are MISSING rows, NOT zero rows. We
build a date→row map and treat absent dates as $0 / 0-tok. So:
  - lifetime    = Σ tokens_total over the PRESENT rows (via ``_as_int`` on STRINGS).
  - record day  = the row with the max ``total_usage`` (via ``_as_float``).
  - streak_run  = walk BACKWARD from the most-recent ACTIVE date counting
                  CONSECUTIVE PRESENT dates — a gap BREAKS it (a non-contiguous
                  set like Jun21 + Jun23 yields run == 1, proving absent dates are
                  treated as zero, not contiguous).

HONESTY-AS-DESIGN (decision B): today's date may have NO row (Jun-24 today), so an
"ongoing streak" is falsifiable. We expose ``streak_is_ongoing_today`` (True ONLY
when the most-recent active date IS today) so the widget can label the LAST run
honestly ("3-DAY RUN" / "RUN ENDED — last 3 days") and NEVER claim an
ongoing-today streak the emptiness contradicts (validation-must-be-deterministic).

Quirks respected (decision E): the time-bucket key is detected via the analytics
``_bucket_key`` regex (NEVER hardcoded — works for ``date__day`` today and a
hypothetical ``created_at__day``); ``total_usage`` is a JSON number
(``_as_float``); ``tokens_total`` / ``request_count`` are JSON STRINGS
(``_as_int``). Never raises.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import List, Optional

# Imported lazily-safe from the analytics module (pure helpers, no Qt, no I/O).
from api_client import _bucket_key, _as_float, _as_int


@dataclass(frozen=True)
class RecordDay:
    """One active day, used for the record "black-box" strip and the dossier
    timeline. ``date`` is the ISO date string ('2026-06-22')."""
    date: str = ""
    spend: float = 0.0
    tokens: int = 0
    reqs: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.date


@dataclass(frozen=True)
class TokenRecord:
    """The widget payload for THE FLIGHT RECORDER.

    lifetime_tokens         : Σ tokens_total over PRESENT rows (the odometer drum).
    lifetime_spend          : Σ total_usage over PRESENT rows ($ — the dossier total).
    lifetime_requests       : Σ request_count over PRESENT rows (the dossier total).
    record                  : the max-by-spend day (the black-box flight strip).
    record_by_tokens        : the max-by-tokens day — exposed so the widget can
                              auto-add a 2nd dimmer strip ONLY when the biggest-
                              TOKEN day differs from the biggest-SPEND day
                              (decision E; today they coincide, so one strip).
    streak_run              : the LAST-ACTIVE-RUN length (consecutive present days
                              walking backward from the most-recent active date).
    streak_is_ongoing_today : True ONLY when the most-recent active date IS today
                              (so the widget labels "ended" honestly otherwise —
                              NEVER an ongoing-today claim, decision B).
    last_active_date        : the most-recent active ISO date ('2026-06-23').
    active_days             : the count of distinct active days in the range.
    series                  : every active day ascending (the dossier timeline,
                              reusing the mini-bar vocabulary).
    """
    lifetime_tokens: int = 0
    lifetime_spend: float = 0.0
    lifetime_requests: int = 0
    record: RecordDay = field(default_factory=RecordDay)
    record_by_tokens: RecordDay = field(default_factory=RecordDay)
    streak_run: int = 0
    streak_is_ongoing_today: bool = False
    last_active_date: str = ""
    active_days: int = 0
    series: List[RecordDay] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Key present but zero active days -> the tidy 'No traffic logged yet'
        instrument (real zeros, honest — distinct from the LOCKED sentinel which
        the widget owns)."""
        return self.active_days == 0

    @property
    def has_second_strip(self) -> bool:
        """True when the biggest-TOKEN day differs from the biggest-SPEND day, so
        a 2nd dimmer flight strip is warranted (decision E). Today they coincide
        (Jun-22 is both), so this is False and one strip shows."""
        return (not self.record.is_empty and not self.record_by_tokens.is_empty
                and self.record_by_tokens.date != self.record.date)


def _day_map(rows: list, bucket_key: Optional[str]):
    """Build an ISO-date -> row map over the active days (decision B — the query
    omits zero-days, so this map's keys ARE the active days; absent dates are
    treated as $0/0-tok by their absence). The date is the bucket value's first
    10 chars ('2026-06-22'). Rows missing the bucket key are skipped (never
    silently counted as a phantom day)."""
    out: dict = {}
    for row in rows:
        raw = row.get(bucket_key) if bucket_key else None
        if not raw:
            continue
        iso = str(raw)[:10]
        # On the rare chance two rows share a day (shouldn't with dims=[]), keep
        # the first deterministically.
        out.setdefault(iso, row)
    return out


def _last_active_run(present_dates: List[str], day_map: dict) -> int:
    """Walk BACKWARD from the most-recent active date counting CONSECUTIVE present
    dates. A gap (a missing date, i.e. a zero-day) BREAKS the run. Returns 0 when
    there are no active dates.

    This is the gap-aware core that makes the absent-dates-are-zero contract
    deterministic: a non-contiguous set {Jun21, Jun23} yields 1 (Jun23 alone),
    NOT 2, because Jun22 is missing."""
    if not present_dates:
        return 0
    try:
        cur = datetime.date.fromisoformat(present_dates[-1])
    except (ValueError, TypeError):
        return 0
    run = 0
    while cur.isoformat() in day_map:
        run += 1
        cur = cur - datetime.timedelta(days=1)
    return run


def _record_day_from_row(row: dict, bucket_key: Optional[str]) -> RecordDay:
    raw = row.get(bucket_key) if bucket_key else None
    return RecordDay(
        date=str(raw)[:10] if raw else "",
        spend=_as_float(row.get("total_usage")),
        tokens=_as_int(row.get("tokens_total")),
        reqs=_as_int(row.get("request_count")),
    )


def build_token_recorder(rows: list, today: Optional[datetime.date] = None) -> TokenRecord:
    """Pure builder: the lifetime odometer + the record day + the gap-aware
    last-active-run streak.

    rows : the parsed analytics rows from the daily dims=[] full-range query
           (metrics ['total_usage','tokens_total','request_count']).
    today: injectable for deterministic tests; defaults to date.today(). Drives
           ONLY the ``streak_is_ongoing_today`` flag (decision B) — never the run
           length itself.

    Contract (decisions B/E):
      - bucket key detected via _bucket_key (NEVER hardcoded date__day).
      - lifetime_tokens = Σ tokens_total (via _as_int — STRINGS) over present rows.
      - record = max total_usage (via _as_float); record_by_tokens = max tokens.
      - streak_run = consecutive present dates walking backward from the most-
        recent active date (a gap breaks it — absent dates are zero, decision B).
      - streak_is_ongoing_today True ONLY when last_active_date == today.
      - zero active days -> is_empty ('No traffic logged yet').
    Never raises.
    """
    rows = rows or []
    if not rows:
        return TokenRecord()

    bucket_key = _bucket_key(rows[0])
    dmap = _day_map(rows, bucket_key)
    present = sorted(dmap.keys())
    active_days = len(present)
    if active_days == 0:
        return TokenRecord()

    # Lifetime roll-ups over the PRESENT rows (the map's rows — the active days).
    present_rows = [dmap[d] for d in present]
    lifetime_tokens = sum(_as_int(r.get("tokens_total")) for r in present_rows)
    lifetime_spend = sum(_as_float(r.get("total_usage")) for r in present_rows)
    lifetime_requests = sum(_as_int(r.get("request_count")) for r in present_rows)

    # Record day = max total_usage; record-by-tokens latent for the 2nd strip.
    # Tie-break on the later date so a repeated peak picks the most recent.
    record_row = max(present_rows,
                     key=lambda r: (_as_float(r.get("total_usage")),
                                    str(r.get(bucket_key) or "")))
    record_tok_row = max(present_rows,
                         key=lambda r: (_as_int(r.get("tokens_total")),
                                        str(r.get(bucket_key) or "")))

    # The gap-aware last-active-run + the honest ongoing-today flag (decision B).
    streak_run = _last_active_run(present, dmap)
    last_active = present[-1]
    ref_today = (today or datetime.date.today()).isoformat()
    ongoing = (last_active == ref_today)

    series = [_record_day_from_row(dmap[d], bucket_key) for d in present]

    return TokenRecord(
        lifetime_tokens=lifetime_tokens,
        lifetime_spend=lifetime_spend,
        lifetime_requests=lifetime_requests,
        record=_record_day_from_row(record_row, bucket_key),
        record_by_tokens=_record_day_from_row(record_tok_row, bucket_key),
        streak_run=streak_run,
        streak_is_ongoing_today=ongoing,
        last_active_date=last_active,
        active_days=active_days,
        series=series,
    )
