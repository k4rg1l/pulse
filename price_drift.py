"""
OpenRouter Pulse - #8 THE FAULT LINE (Price-Drift Watcher)

The pure, Qt-free engine + persisted store behind the seismograph fault line.

Two halves, both deliberately testable in isolation (no PySide6 import):

1. A PURE DIFF (`diff_endpoints`): given a model's stored BASELINE (a
   {ep_ident: PriceSnap}) and the CURRENT endpoints the card already holds,
   produce a `DriftResult` — the magnitude, the dominant direction, the set of
   moved rows, and a per-tremor read-out. No I/O, no Qt, no globals.

2. A PERSISTED STORE (`PriceSnapshotStore`): a sibling `price_snaps.json` next to
   `state.json`, MIRRORING persistence.py's History/Snapshot idiom (json + atomic
   `.tmp` replace + `utf-8-sig` load + `_prune` + a MAX cap). It holds the
   baseline snapshot per (model_id, ep_ident) and implements the subtle
   BASELINE-UPDATE POLICY (decision C) in `observe()`:

     (i)   FIRST SIGHT of a model (no baseline) -> store current, stay SILENT.
     (ii)  QUIET (no moved rows / magnitude 0)  -> roll baseline FORWARD, None.
     (iii) DRIFT (magnitude > 0)                -> DO NOT overwrite baseline (so
           the crack PERSISTS across refreshes), return the DriftResult with
           is_fresh True only on the refresh it first appears.
     (iv)  acknowledge() (dossier opened)       -> WRITE current as the new
           baseline + clear fresh, so the next observe() diffs current-vs-current
           -> quiet -> the crack clears and the SAME drift never re-fires.

This module reads only floats + ids the card already fetched — never raw network
blobs or secrets (the persistence invariant).

ORCHESTRATOR-LOCKED DECISIONS implemented here:
  A. MAGNITUDE: price_mag = clamp(2.0 * abs(new-old)/old, 0, 1). A structural
     event (derank flip True, or a NEW endpoint priced below the stored minimum)
     forces magnitude = max(magnitude, STRUCTURAL_FLOOR=0.6). Card magnitude =
     max over moved rows. DIRECTION: adverse if a price ROSE or a provider
     DERANKED; favorable if a price FELL or a cheaper provider appeared; the net
     pole = whichever dominates (tie -> adverse, the "watch out" default).
  B. NOISE GATE: a relative move < 0.01 (1%) is ignored (float jitter).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from persistence import state_dir

log = logging.getLogger("pulse.drift")

# ---- tunables (decisions A & B — fixed, not settings) ----
NOISE_GATE = 0.01        # relative price move below this is ignored (jitter)
STRUCTURAL_FLOOR = 0.6   # a derank flip / cheaper-appeared forces >= this
MAG_SCALE = 2.0          # 2 * |Δ|/old  (22% move -> 0.44; halving -> clamp 1.0)

# Direction poles (the engine speaks plain strings; the card maps them to hue).
ADVERSE = "adverse"      # price rose, or a provider deranked  -> seismic-amber
FAVORABLE = "favorable"  # price fell, or a cheaper one surfaced -> quartz-violet

# Tremor kinds (drives the dossier glyph + wording).
KIND_PRICE_UP = "price_up"
KIND_PRICE_DOWN = "price_down"
KIND_CHEAPER = "cheaper_appeared"
KIND_DERANK = "deranked"

RETENTION_DAYS = 120
MAX_MODELS = 2_000       # hard cap on tracked models (ids only — tiny)


def price_snaps_path() -> Path:
    return state_dir() / "price_snaps.json"


@dataclass
class PriceSnap:
    """One provider's stored pricing fingerprint for a model (the unit we diff).

    prompt/completion are $/token floats (mirroring EndpointInfo.pricing_*);
    is_deranked is the structural flag (frontend EndpointRef.is_deranked — False
    on every live endpoint today, proven by a synthetic fixture). `name` is kept
    only for the dossier read-out (HTML-escaped at render)."""
    prompt: float = 0.0
    completion: float = 0.0
    is_deranked: bool = False
    name: str = ""
    ts: float = 0.0


@dataclass
class Tremor:
    """One row's movement, the dossier's per-line read-out."""
    ident: str
    kind: str                 # KIND_*
    direction: str            # ADVERSE | FAVORABLE
    magnitude: float          # 0..1 (this row's own magnitude)
    name: str = ""            # provider display name (escaped at render)
    old: float = 0.0          # old $/token (prompt), for the read-out
    new: float = 0.0          # new $/token (prompt)
    rel: float = 0.0          # signed relative change (new-old)/old, 0 for structural


@dataclass
class DriftResult:
    """The card-level read for one model. magnitude==0 / empty moved_rows means
    QUIET (the card paints NOTHING). is_fresh rides the shimmer for one refresh."""
    magnitude: float = 0.0
    direction: str = ADVERSE
    moved_rows: set = field(default_factory=set)   # set[ident]
    tremors: list = field(default_factory=list)    # list[Tremor], largest first
    is_fresh: bool = False
    baseline_ts: float = 0.0                        # ts of the snapshot we diffed against

    @property
    def quiet(self) -> bool:
        return self.magnitude <= 0.0 or not self.moved_rows


# ---------------------------------------------------------------------------
#  Pure helpers
# ---------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def price_magnitude(old: float, new: float) -> float:
    """Decision A: clamp(2 * |new-old|/old, 0, 1). old<=0 -> 0 (can't form a
    ratio; a price appearing from nothing is handled as a structural event)."""
    if old <= 0.0:
        return 0.0
    return _clamp01(MAG_SCALE * abs(new - old) / old)


def _ep_ident(ep) -> str:
    """Mirror PinnedModelCard._ep_ident: the stable per-row identity."""
    return getattr(ep, "tag", "") or getattr(ep, "provider_name", "")


def _ep_deranked(ep) -> bool:
    """Read is_deranked from whatever per-endpoint data is available (decision
    D). EndpointInfo does NOT carry it today (it lives on the frontend
    EndpointRef), so default False — the derank PATH is proven by a synthetic
    fixture (a stored snap flipping False -> True), not live plumbing."""
    return bool(getattr(ep, "is_deranked", False))


def snap_from_ep(ep) -> PriceSnap:
    """Build a PriceSnap from a live EndpointInfo. float()s every price (they may
    arrive as strings upstream; the card already float()s prompt/completion, but
    be defensive here so the store is always numeric)."""
    def f(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0
    return PriceSnap(
        prompt=f(getattr(ep, "pricing_prompt", 0.0)),
        completion=f(getattr(ep, "pricing_completion", 0.0)),
        is_deranked=_ep_deranked(ep),
        name=getattr(ep, "provider_name", "") or "",
        ts=time.time(),
    )


def snapshot_endpoints(endpoints) -> dict:
    """{ep_ident: PriceSnap} for a model's current endpoint list."""
    out: dict = {}
    for ep in (endpoints or []):
        ident = _ep_ident(ep)
        if not ident:
            continue
        out[ident] = snap_from_ep(ep)
    return out


# ---------------------------------------------------------------------------
#  THE PURE DIFF (no Qt, no I/O) — decisions A & B
# ---------------------------------------------------------------------------

def diff_snaps(baseline: dict, current: dict) -> DriftResult:
    """Diff a stored BASELINE {ident: PriceSnap} vs CURRENT {ident: PriceSnap}.

    Returns a DriftResult. A QUIET diff (no moved rows past the noise gate, no
    structural event) yields magnitude 0 / empty moved_rows. is_fresh is left
    False here — the STORE decides freshness (decision C, it's a temporal fact
    the pure diff can't know)."""
    tremors: list[Tremor] = []
    moved: set[str] = set()
    adverse_mag = 0.0
    favorable_mag = 0.0

    # stored minimum prompt price across the baseline — the bar a "cheaper
    # appeared" new endpoint must beat (decision A, the favorable structural).
    base_prices = [s.prompt for s in baseline.values() if s.prompt > 0]
    base_min = min(base_prices) if base_prices else None

    for ident, cur in current.items():
        old = baseline.get(ident)
        if old is None:
            # A NEW endpoint. Only a structural FAVORABLE event if it undercuts
            # the stored minimum (a genuinely cheaper provider surfaced). A new
            # endpoint that is NOT cheaper is not a tremor (no baseline to rise
            # from) — it just gets absorbed into the baseline next quiet roll.
            if base_min is not None and cur.prompt > 0 and cur.prompt < base_min:
                mag = STRUCTURAL_FLOOR
                favorable_mag = max(favorable_mag, mag)
                moved.add(ident)
                tremors.append(Tremor(
                    ident=ident, kind=KIND_CHEAPER, direction=FAVORABLE,
                    magnitude=mag, name=cur.name, old=base_min, new=cur.prompt,
                    rel=0.0))
            continue

        # DERANK structural (decision A): is_deranked flips False -> True is
        # adverse and forces magnitude >= STRUCTURAL_FLOOR.
        if cur.is_deranked and not old.is_deranked:
            mag = STRUCTURAL_FLOOR
            adverse_mag = max(adverse_mag, mag)
            moved.add(ident)
            tremors.append(Tremor(
                ident=ident, kind=KIND_DERANK, direction=ADVERSE,
                magnitude=mag, name=cur.name, old=old.prompt, new=cur.prompt,
                rel=0.0))
            # a deranked row may ALSO have moved on price; fall through to also
            # record the price move so the dossier is complete.

        # PRICE move on the prompt price (the headline axis). Noise-gated.
        if old.prompt > 0:
            rel = (cur.prompt - old.prompt) / old.prompt
            if abs(rel) >= NOISE_GATE:
                mag = price_magnitude(old.prompt, cur.prompt)
                if mag > 0:
                    moved.add(ident)
                    if rel > 0:
                        adverse_mag = max(adverse_mag, mag)
                        tremors.append(Tremor(
                            ident=ident, kind=KIND_PRICE_UP, direction=ADVERSE,
                            magnitude=mag, name=cur.name,
                            old=old.prompt, new=cur.prompt, rel=rel))
                    else:
                        favorable_mag = max(favorable_mag, mag)
                        tremors.append(Tremor(
                            ident=ident, kind=KIND_PRICE_DOWN, direction=FAVORABLE,
                            magnitude=mag, name=cur.name,
                            old=old.prompt, new=cur.prompt, rel=rel))

    magnitude = max(adverse_mag, favorable_mag)
    # Net dominant pole: whichever side has the larger magnitude; tie -> adverse
    # (decision A — "watch out" is the safer watcher default).
    direction = ADVERSE if adverse_mag >= favorable_mag else FAVORABLE
    # largest tremor first (drives the epicenter + the dossier ordering).
    tremors.sort(key=lambda t: t.magnitude, reverse=True)
    base_ts = max((s.ts for s in baseline.values()), default=0.0)
    return DriftResult(magnitude=magnitude, direction=direction,
                       moved_rows=moved, tremors=tremors, is_fresh=False,
                       baseline_ts=base_ts)


def diff_endpoints(baseline: dict, endpoints) -> DriftResult:
    """Convenience: diff a stored baseline vs a LIVE endpoint list."""
    return diff_snaps(baseline, snapshot_endpoints(endpoints))


# ---------------------------------------------------------------------------
#  THE PERSISTED STORE (mirrors persistence.py) — decision C lives here
# ---------------------------------------------------------------------------

class PriceSnapshotStore:
    """Persisted baselines keyed by model_id -> {ep_ident: PriceSnap}, plus a
    per-model `is_fresh` flag (so a persisted drift stays fresh-marked only on
    the refresh it first appeared). Atomic json, BOM-tolerant load, pruned.

    The store, NOT the card, owns the baseline-update policy (decision C):
    `observe()` is the single entry point the dashboard calls each refresh."""

    def __init__(self, baselines: dict | None = None, fresh: dict | None = None):
        # {model_id: {ident: PriceSnap}}
        self.baselines: dict[str, dict[str, PriceSnap]] = baselines or {}
        # {model_id: bool} — True while a freshly-detected drift is unacked
        self._fresh: dict[str, bool] = fresh or {}

    # ---- load / save (mirror persistence.History) ----

    @classmethod
    def load(cls) -> "PriceSnapshotStore":
        path = price_snaps_path()
        if not path.exists():
            return cls()
        try:
            # utf-8-sig handles a BOM if the file is hand-edited / PS-rewritten.
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            baselines: dict[str, dict[str, PriceSnap]] = {}
            for mid, rows in (data.get("baselines") or {}).items():
                baselines[mid] = {ident: PriceSnap(**snap)
                                  for ident, snap in (rows or {}).items()}
            fresh = dict(data.get("fresh") or {})
            return cls(baselines, fresh)
        except Exception as e:
            # Corrupt file — start fresh but never crash (mirrors History.load).
            log.warning("load error: %s; starting fresh", e)
            return cls()

    def save(self) -> None:
        self._prune()
        payload = {
            "version": 1,
            "baselines": {
                mid: {ident: asdict(snap) for ident, snap in rows.items()}
                for mid, rows in self.baselines.items()
            },
            "fresh": {mid: v for mid, v in self._fresh.items() if v},
        }
        tmp = price_snaps_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")),
                       encoding="utf-8")
        tmp.replace(price_snaps_path())

    def _prune(self) -> None:
        cutoff = time.time() - RETENTION_DAYS * 86400
        # Drop a model entirely if EVERY stored snap is older than the cutoff
        # (a model the user unpinned long ago). A live model is re-touched every
        # refresh so it never expires.
        for mid in list(self.baselines.keys()):
            rows = self.baselines[mid]
            if rows and all(s.ts < cutoff for s in rows.values()):
                self.baselines.pop(mid, None)
                self._fresh.pop(mid, None)
        if len(self.baselines) > MAX_MODELS:
            # keep the most-recently-touched models
            ranked = sorted(
                self.baselines.items(),
                key=lambda kv: max((s.ts for s in kv[1].values()), default=0.0),
                reverse=True)
            keep = dict(ranked[:MAX_MODELS])
            self.baselines = keep
            self._fresh = {m: v for m, v in self._fresh.items() if m in keep}

    # ---- the baseline-update policy (decision C) ----

    def observe(self, model_id: str, endpoints) -> DriftResult | None:
        """Diff CURRENT endpoints vs the stored baseline for `model_id` and apply
        the baseline-update policy. Returns the DriftResult to push to the card,
        or None when the card should paint NOTHING (first-sight / quiet).

        Mutates the in-memory store; the caller persists with save() after."""
        current = snapshot_endpoints(endpoints)
        if not current:
            return None

        baseline = self.baselines.get(model_id)

        # (i) FIRST SIGHT — no baseline. Store current, stay SILENT (no phantom
        #     quake). Clear any stale fresh flag.
        if not baseline:
            self.baselines[model_id] = current
            self._fresh.pop(model_id, None)
            return None

        result = diff_snaps(baseline, current)

        if result.quiet:
            # (ii) QUIET — roll the baseline FORWARD to current, no drift. This is
            #      also the post-acknowledge path: once acknowledge() wrote
            #      current as the baseline, the next observe() diffs
            #      current-vs-current -> quiet -> the crack clears here.
            self.baselines[model_id] = current
            self._fresh.pop(model_id, None)
            return None

        # (iii) DRIFT — do NOT overwrite the baseline (so the crack PERSISTS
        #       across refreshes until acknowledged). The _fresh dict does
        #       double duty: a model is PRESENT in it while a drift persists, and
        #       its VALUE is is_fresh. So is_fresh is True only on the refresh the
        #       drift first appears (the model isn't in _fresh yet), then False
        #       on every subsequent refresh while it persists.
        already_persisting = model_id in self._fresh
        result.is_fresh = not already_persisting
        self._fresh[model_id] = result.is_fresh
        if result.is_fresh:
            log.info("drift: %s mag=%.2f dir=%s rows=%d",
                     model_id, result.magnitude, result.direction,
                     len(result.moved_rows))
        return result

    def acknowledge(self, model_id: str, endpoints) -> None:
        """Decision C (iv): the dossier opened — WRITE current as the new
        baseline and clear fresh. The next observe() then diffs
        current-vs-current -> quiet -> the crack clears and the SAME drift never
        re-fires. MUST be followed by save() so it survives a restart."""
        current = snapshot_endpoints(endpoints)
        if current:
            self.baselines[model_id] = current
        # Clear fresh AND remove from the persisting set so the next observe()
        # treats the (now identical) baseline as a clean quiet roll.
        self._fresh.pop(model_id, None)

    def is_fresh(self, model_id: str) -> bool:
        return bool(self._fresh.get(model_id, False))

    def baseline_for(self, model_id: str) -> dict:
        return self.baselines.get(model_id, {})
