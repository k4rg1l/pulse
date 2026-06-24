"""#15 THE ASSAY — the pure value-compute layer (no Qt, no I/O).

The Insights zone's always-live USER-key anchor. Each pinned model is scored by
quality-per-dollar: an Artificial-Analysis benchmark index (intelligence / coding
/ agentic, 0-100) divided by the model's CHEAPEST priced prompt $/Mtok — the SAME
number the pinned card's PRICE column shows, so the math is auditable.

This module is intentionally dependency-light (mirrors spend_palette's no-QWidget
discipline) so it can be imported by dashboard.py, widgets.py, AND unit tests
without a cycle and unit-tested with zero Qt. The render widget (ValueAssayWidget,
widgets.py) and the dashboard wiring (_distribute_value, dashboard.py) both consume
AssayResult / value_rank().

SCALE HONESTY (decision C): a model that lacks the ACTIVE AA index is marked
`unassayable` (a hollow coin on the rail) — it is NEVER silently dropped and NEVER
ELO-substituted on the main rail (mixing ELO ~1300 with AA 0-100 on one axis is
dishonest). peak_elo is carried only so the certificate popup can show a clearly
LABELLED "ELO basis" fallback; it never becomes a coin diameter.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# The metric cycle order (decision E): intelligence -> coding -> agentic, with
# agentic the recon-confirmed default (the value story is told on agentic).
METRICS = ("intelligence", "coding", "agentic")
DEFAULT_METRIC = "agentic"


@dataclass
class AssayModel:
    """One pinned model's assay row, ready for the widget.

    value_by_metric maps each AA metric name -> value (quality/$Mtok) or None
    when that index is missing. `value` is the value for the ACTIVE metric (None
    => unassayable on this metric -> hollow coin). `quality` is the active AA
    index itself (for the certificate footnote). `rank` is the value rank in the
    ACTIVE metric (0 == best value == gold == hallmark); models unassayable on
    the active metric sort to the end and get rank -1.
    """
    model_id: str
    display: str
    provider: str = ""                       # provider of the cheapest prompt endpoint
    price: Optional[float] = None            # cheapest prompt $/Mtok (the denominator)
    quality_by_metric: dict = field(default_factory=dict)  # metric -> AA index or None
    value_by_metric: dict = field(default_factory=dict)    # metric -> value or None
    peak_elo: Optional[int] = None           # certificate-only ELO fallback (NEVER a coin)
    spend_rank: int = 0                       # position in the pin list (drives model_color rim)
    # filled by value_rank() for the active metric:
    value: Optional[float] = None
    quality: Optional[float] = None
    rank: int = -1                            # 0 == best value; -1 == unassayable
    unassayable: bool = False                 # True => hollow coin (no active AA index)


@dataclass
class AssayResult:
    """The widget payload: the active-metric-sorted model list + the headline
    facts the widget paints without recomputing.

    models: assayable models first (descending value, rank 0 first), then the
            unassayable (hollow) models. Empty => the 0-pin "pin a model" state.
    metric: the active metric the values/sort reflect.
    top_multiple: topValue / secondValue across the ASSAYABLE models (the engraved
                  '4.8×' on the gold coin), or None when <2 assayable models.
    """
    models: list = field(default_factory=list)
    metric: str = DEFAULT_METRIC
    top_multiple: Optional[float] = None

    @property
    def assayable(self) -> list:
        return [m for m in self.models if not m.unassayable]

    @property
    def is_empty(self) -> bool:
        return len(self.models) == 0

    @property
    def winner(self) -> Optional[AssayModel]:
        a = self.assayable
        return a[0] if a else None


def cheapest_prompt_price(endpoints) -> tuple[Optional[float], str]:
    """The denominator: the minimum priced prompt $/Mtok across a model's
    endpoints, plus the provider name of that cheapest endpoint.

    `endpoints` is a ModelEndpoints (has `.endpoints`, a list of EndpointInfo) or
    None. Reads EndpointInfo.price_per_mtok_prompt (== pricing_prompt*1e6) — the
    EXACT field the card's PRICE column uses — and ignores zero/None prices
    (an unpriced endpoint is not a $0 deal). Returns (None, "") when no priced
    prompt endpoint exists yet (endpoints still loading -> hollow / last-good).
    """
    eps = getattr(endpoints, "endpoints", None) if endpoints is not None else None
    if not eps:
        return None, ""
    best_price = None
    best_provider = ""
    for e in eps:
        p = getattr(e, "price_per_mtok_prompt", None)
        if p is None or p <= 0:
            continue
        if best_price is None or p < best_price:
            best_price = p
            best_provider = getattr(e, "provider", "") or ""
    return best_price, best_provider


def build_assay_model(model_id: str, display: str, entry, endpoints,
                      spend_rank: int = 0) -> AssayModel:
    """Compute one model's full assay row from its BenchmarkEntry (`entry`, may be
    None) and its ModelEndpoints (`endpoints`, may be None).

    value[metric] = entry.<metric> / cheapest_prompt_price; None when either the
    AA index OR the price is missing. Pure — no Qt, no network.
    """
    price, provider = cheapest_prompt_price(endpoints)
    quality_by_metric: dict = {}
    value_by_metric: dict = {}
    for m in METRICS:
        q = getattr(entry, m, None) if entry is not None else None
        quality_by_metric[m] = q
        if q is not None and price is not None and price > 0:
            value_by_metric[m] = q / price
        else:
            value_by_metric[m] = None
    return AssayModel(
        model_id=model_id,
        display=display,
        provider=provider,
        price=price,
        quality_by_metric=quality_by_metric,
        value_by_metric=value_by_metric,
        peak_elo=(entry.peak_elo if entry is not None else None),
        spend_rank=spend_rank,
    )


def value_rank(models: list, metric: str = DEFAULT_METRIC) -> AssayResult:
    """Rank `models` (list[AssayModel]) by their ACTIVE-metric value and assemble
    the widget payload.

    - Each model's `.value`/`.quality`/`.unassayable` are stamped for `metric`.
    - Assayable models (value not None) sort DESCENDING by value -> rank 0,1,2…
      (rank 0 == best deal == gold + hallmark).
    - Unassayable models (no active AA index) keep rank -1 and sort to the end
      (hollow coins on the rail — NEVER dropped, NEVER ELO-substituted here).
    - top_multiple = value[rank0] / value[rank1] across assayable models (the
      engraved '×'); None when fewer than 2 assayable models (decision D: 1 pin
      -> no hallmark/×).
    """
    if metric not in METRICS:
        metric = DEFAULT_METRIC

    # Stamp the active-metric fields on each model (don't mutate ordering yet).
    for m in models:
        v = m.value_by_metric.get(metric)
        m.value = v
        m.quality = m.quality_by_metric.get(metric)
        m.unassayable = v is None
        m.rank = -1

    assayable = [m for m in models if not m.unassayable]
    unassayable = [m for m in models if m.unassayable]
    # Descending value; model_id as a stable deterministic tiebreaker.
    assayable.sort(key=lambda m: (-(m.value or 0.0), m.model_id))
    for i, m in enumerate(assayable):
        m.rank = i

    top_multiple = None
    if len(assayable) >= 2:
        top = assayable[0].value or 0.0
        second = assayable[1].value or 0.0
        if second > 0:
            top_multiple = top / second

    return AssayResult(
        models=assayable + unassayable,
        metric=metric,
        top_multiple=top_multiple,
    )


def log_scale(value: float, vmin: float, vmax: float) -> float:
    """Map a value onto [0,1] on a LOG axis: (ln v - ln min)/(ln max - ln min).

    Used for both the coin DIAMETER and its rail x-position so the gold coin
    towers honestly over the copper one even when the linear ratio is large.
    Guards: non-positive inputs and a degenerate min==max range both clamp to a
    centred 0.5 (the single/equal-value guard -> all coins the same mid size).
    The result is clamped to [0,1].
    """
    if value is None or vmin is None or vmax is None:
        return 0.5
    if value <= 0 or vmin <= 0 or vmax <= 0:
        return 0.5
    if vmax <= vmin:
        return 0.5
    t = (math.log(value) - math.log(vmin)) / (math.log(vmax) - math.log(vmin))
    return max(0.0, min(1.0, t))
