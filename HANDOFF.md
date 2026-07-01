# HANDOFF.md — start here

**The living "you are here" doc.**
- **New agent?** Read THIS top-to-bottom first, then [AGENTS.md](AGENTS.md). That's the whole onboarding — you'll know the vision, the current state, and exactly what to build next.
- **Outgoing agent?** Update this as your *last act*: Status, what you shipped, what's next, any new decision/gotcha. When something becomes a permanent rule, move it into AGENTS.md. Keep **THE VISION** section stable.

**Last updated:** 2026-06-24 · **Phase A COMPLETE.** All 18 OpenRouter roadmap features (#1–#18) + all 4 foundations (F1–F4) are shipped on branch `openrouter-roadmap` (off `main`). **599 tests green.** Branch left for review — NOT merged, NOT pushed, NOT released. **NEXT: Phase B — Claude deep-dive** (see [docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md)), on a fresh branch off `main`.

---

## Cleanup pass - 2026-06-30 (autonomous, branch `openrouter-roadmap`)

A debloat / DRY / de-monolith / efficiency pass (13 commits, `13f76d1`..`a101d29`).
**609 tests green** (was 599); every commit security-reviewed. Behaviour-preserving
throughout - **no UI/sizing changes** (Phase 4 is owner-driven, below).

**Done:**
- **Phase 0 debloat:** removed ~710 LOC dead code (6 unused widget classes, 3
  unwired api_client chains, dead constants/imports/symbols). Fixed 2 real bugs:
  the low-balance alert now honours `balance_warning`/`balance_critical` (was
  hardcoded config constants); guarded a `key_refresh_seconds=0` 0ms-timer loop.
- **Phase 1 DRY:** `num.py` (shared coercion) + `FrontendClient._fetch` (7 clone
  wrappers -> 1, +4 tests); `PopupStrip` base for the 12 popup strips (-230 LOC,
  + fixed a HiDPI-blur bug on 2 stale strips); `BaseCard` for GPU/System/Claude
  source cards (+ a ClaudeCard paint smoke test); pricing-block dedup.
- **Phase 2 de-monolith:** extracted `spend_model.py` (~1,050 pure LOC) from
  `api_client.py` (2,707 -> 1,510), re-exported so every import still resolves.
- **Phase 3 efficiency:** bounded the `AnalyticsClient` cache (was an unbounded
  memory leak, +2 tests); the permaslug resolver is fetched once per refresh
  (was doubling the per-model uptime fan-out).

**Update 2026-07-01 (round 2):** resumed autonomously; shipped 2 more Phase-1 DRY
commits — `c04c638` (Colors.CRIMSON + `_alpha()` helper, 82 `setAlpha` sites) and
`efe039f` (`_strip_img_div()` collapsing the 12 strip-embed blocks). So of the
Phase-1 items below, **`_alpha`/CRIMSON/`strip_to_img_html` are now DONE**; `_elide`
was intentionally SKIPPED (its sites reuse an existing QFontMetrics, so a helper
would ADD allocations). **16 commits total; 611 tests green.** The `PinnedModelCard`
split was re-evaluated and kept deferred: an ~800-LOC move on the core surface whose
dossier HTML isn't unit-tested — it wants visual QA (open the popups) even though a
same-file mixin would be runtime-safe. Everything still listed below is deferred.

**Deferred (safe but not done - for owner review):**
- *Phase 1:* ~~`_alpha`/`strip_to_img_html`/`CRIMSON`~~ DONE (round 2). `_elide()`
  (~30 sites) SKIPPED — those sites reuse an existing QFontMetrics, so a helper
  would ADD per-call allocations (not a clean win).
- *Color tokens:* ~348 hex literals (only 27 in `theme.py`) -> route through
  `theme.Colors` at IDENTICAL values. Behaviour-preserving but large, and it's the
  prerequisite for Phase-4 theming - do it WITH the UI overhaul.
- *Phase 2:* `PinnedModelCard` (~3.2k-line class) -> a `Band` interface (8 bands)
  + a `DossierFormatter` (~950 LOC of HTML f-strings). Big; do with visual review
  (it's the core OpenRouter surface). Then split `widgets.py` (~11k LOC) into a pkg.
- *Phase 3:* GPU/System declare 2-3s `poll_interval` but `main.py` floors to 15s
  (pick the intended live cadence - a resource tradeoff, owner's call); cache
  per-paint `QFont`/`QFontMetrics`; `_paint_fault_line` rebuilds its cached path
  every paint (honour the cache); `config.py` parses `settings.json` 3x at import
  (-> once, but tread carefully: this is the 401/empty-key path); boot-path resolver
  can double-fire if speed off + trend+uptime on (narrow edge, not the refresh one).

**FYI:** `tools/_probe_out/_probe_ghost_13.py` calls the removed `GhostDiff`
`*_pairs()` methods - that dir is gitignored scratch (not shipped, not tested); harmless.

**NEXT = Phase 4: the UI / sizing overhaul - OWNER-DRIVEN.** The foundation the
owner asked for: a `metrics` module configured once in `main.py` (right after
`QApplication(sys.argv)`, before `Dashboard(...)`) deriving one base unit from
`QScreen.availableGeometry()` + DPI; then a spacing/type/radius scale in `theme.py`;
then dashboard size-to-content (kills the empty void on the GPU/System/Claude tabs);
then the visual cleanup (redundancy - e.g. "next top-up" printed 3x; the dense
provider wall; uneven rhythm; the too-bright active nav slot). Everything today is
import-time fixed px (`config.DASHBOARD_WIDTH=560`, `setFixedWidth`-locked). Do NOT
start until the owner drives it (they will nitpick UI heavily - it is theirs).

> **Validating Phase A?** See **[docs/PHASE-A-VALIDATION.md](docs/PHASE-A-VALIDATION.md)** — the complete cold-start QA playbook (per-feature table, toggles, log markers, honesty contract, known non-blockers).

---

## ★ THE VISION — read once; it's the *why* behind everything

**What Pulse is.** A developer's at-a-glance command center for everything they watch all day — AI spend, usage limits, machine vitals — in one beautiful, native, keyboard-summonable panel that stays out of the way. Source-agnostic: OpenRouter, Claude, GPU, System are equal peers; more can join on one contract.

**The mission right now.** Go **DEEP per source**, starting with **OpenRouter**. Turn each source from a basic readout into a rich, insightful, genuinely *delightful* panel. OpenRouter is the proving ground; Claude / GPU / System follow once the pattern is proven.

**The bar: every feature must be WILD.** Not the standard way everyone implements it — cool, creative, fun, beautiful, and genuinely useful. *Never* basic. The user's words: make it **"gaand faad de"** — jaw-dropping. The reference is **The Arena** (v0.8): instead of showing a model's benchmark as a number in a pill, we made each pinned model a *competitive esports rank* — a painted tier crest (Bronze→Champion), a global rank we compute ourselves, an animated shimmer, and a click-through "Fighter Card" with a lifetime tournament medal haul. **Match or beat that level of imagination on every feature.** If your idea is the obvious one, throw it out and find the one that makes the user grin.

**Move fast, but iterate small and validated.** Ship one cohesive feature — or a tight bundle that shares infrastructure — at a time, and *validate it rigorously before the next*. The user would far rather have small, working, perfected increments than a pile of half-validated changes where bugs hide ("I'd rather keep the flow small and workable than figure out where 100 bugs are coming from"). Perfect it, slowly and iteratively.

**Validation is deterministic and honest.** Clear, *measurable* evidence beats flaky GUI clicking, every time. For a widget bug, write a headless test (the `qapp` offscreen fixture) that measures the exact failure condition — e.g. assert `widget.x()` doesn't move on toggle — reproduce the bug deterministically FIRST, then prove the fix flips that measurement, then lock it with a regression test. **If you don't see clear evidence something works, it probably doesn't — or you haven't tested it properly.** Never claim success you can't show. (This is hard-won: a round of screenshot-clicking "validation" was wrong twice before a deterministic test found the real cause.)

**Professional, industry-grade quality** — in code, UI, and docs.

**The map is already drawn.** OpenRouter's entire API surface is reverse-engineered and the feature set is curated + deduped:
- **[docs/OPENROUTER-RESEARCH.md](docs/OPENROUTER-RESEARCH.md)** — the full capability inventory (three tiers: user-key public API, management-key Analytics API, the no-auth `/api/frontend/v1/*` website API). The "deepest darkest secrets."
- **[docs/OPENROUTER-ROADMAP.md](docs/OPENROUTER-ROADMAP.md)** — the 18-feature build order in 3 waves, with shared foundations, per-feature data sources, effort, and a dedup ledger.

Follow the roadmap. **Re-verify any endpoint live right before building it** — the API drifts (`tools/or_probe.py` / `tools/or_probe_frontend.py` are the re-verification tools; captures land in the gitignored `tools/_probe_out/`).

---

## Status — where we are

- **Released `v0.8.0`** (`origin/main`, tagged, GitHub release w/ binary): **The Arena** + **structured logging**.
- **On `main` (unpushed since v0.8.0):** F2 + #2 The Ledger + #4 Speed Percentile + app-wide pixel-alignment pass.
- **On branch `openrouter-roadmap` (off `main`) — Phase A COMPLETE, NOT merged/pushed/released:**
  - **Wave 1 — Pinned-card enrichments:** #1 The Arena (F4), #2 The Ledger (F2), #3 The Pulse (73h Uptime Ribbon), #4 Speed Percentile, #5 The Threshold / Cheapest Door (F1), #6 The Waterline / Hidden-Cost Badges, #7 The Tape / Trending Arrow, #8 The Fault Line / Price-Drift Watcher.
  - **Wave 2 — Ground-truth Spend zone:** #9 The Spectrum / Spend X-Ray (F3 AnalyticsClient — `AnalyticsClient`: `/analytics/meta` + cached `/analytics/query`, management key; SPEND zone replaces the estimated Usage/Burn-Rate), #10 The Till Roll / Per-Request Receipt, #11 The Autopsy / Spend Autopsy, #12 The Rebate Stub / Cache & Reasoning Savings, #13 The Séance / Ghost Model Detector, #14 The Hourglass / Budget Burn-Down.
  - **Wave 3 — Insights zone:** #15 The Assay / Value Index (+ Insights scaffold), #16 The Title Belt / Model of the Week, #17 The Flight Recorder / Token Odometer + Records, #18 The Court & The Climb / Task Crown + Out-tokened X.
  - **New top-level pure modules:** `price_drift.py`, `spend_palette.py`, `value_assay.py`, `model_of_week.py`, `token_recorder.py`, `task_court.py`.
  - **Mgmt-key features:** render live on this machine (a `management_api_key` is present); degrade to honest locked/young-account states (zero fake data) when absent.
- **Green:** `python -m pytest -q` → **599 passed** (was 205 at branch start).
- **Gotcha banked (see AGENTS.md + memory):** Pulse 401 / $0.00 ⇒ `config.API_KEY` resolved **empty** — env `OPENROUTER_API_KEY` first, then `settings.json`. A persistent User-scope `OPENROUTER_API_KEY` is now set on this machine so `pulse-rebuild` launches authed. **Also:** assistant tool reads can be an isolated FS snapshot ≠ the user's live machine — verify from the user's own shell.
- **The OpenRouter roadmap is DONE.** Next is Phase B (Claude deep-dive) on a fresh branch off `main`.

---

## ▶ THE NEXT BUILD — Phase B: Claude deep-dive

**Phase A is complete.** Branch `openrouter-roadmap` holds all 18 features + F1–F4 and is left for review.

The next phase runs through an **Orchestrator agent** — full operating manual in **[docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md)** (that file IS the prompt). It *manages*; it does not code. It spawns high-effort Opus 4.8 worker agents with detailed, self-contained specs and keeps its own context lean.

**Phase B — Claude deep-dive.** Back to `main` → new branch → reverse-engineer Claude's local data + APIs as thoroughly as OpenRouter (grow [docs/CLAUDE-LOCAL-DATA.md](docs/CLAUDE-LOCAL-DATA.md) into a full `docs/CLAUDE-RESEARCH.md` + a curated wild-feature `docs/CLAUDE-ROADMAP.md`), then orchestrate building those.

### The build flow for each enrichment (non-negotiable — every worker follows this)
pure parser unit-tested against a captured sample → render (font-metric-driven; reuse `widgets.py` patterns) → **deterministic validation** (a `qapp` test that measures the result) + a careful live check → **/security-review (Sonnet over `git diff --staged`)** → commit. One enrichment, one commit. **The user does the visual QA — keep it fast + precise; never screenshot-click.**

### Lessons banked (read these; pass them to every worker)
- **Re-verify endpoints live first** — the frontend API drifts; `stats/*` 404 on the public slug (need the versioned permaslug). Tools: `tools/or_probe_frontend.py`; captures land in gitignored `tools/_probe_out/`.
- **The frontend API bot-blocks the default `python-requests` UA** (connection reset, not 403). `FrontendClient` overrides the UA directly — `setdefault` does NOT work. Locked by `test_frontend_client_overrides_default_user_agent`. Invisible to parser tests — only a live boot caught it.
- **Bands share the provider-row left rail** — crest/speed emblems use `PinnedModelCard._icon_col_cx()`; their text + provider names use `_content_col_x()`; vertical rhythm via `BAND_GAP`/`ROWS_GAP`. New bands MUST use the same rail (locked by `test_crest_and_speed_bands_share_columns`).
- **Logos render in the popup only** (a judge panel + the 14px row both rejected logo-on-row as mush). The board hero is the painted emblem/seal.
- **Single-instance mutex bites dev loops** — a leaked instance holds `Global\Pulse_SingleInstance_v1`; later boots silently exit "already running". `pulse-rebuild` clean-kills first; if a boot logs nothing, kill stray `python …main.py` / `Pulse.exe`.
- **401 / $0.00 ⇒ an *empty* API key**, not an invalid one. `config.py` reads env `OPENROUTER_API_KEY` first, then `settings.json`. Verify from the *launch* shell: `python -c "import config;print(len(config.API_KEY))"` (0 = no key). A persistent User-scope env var is set on this machine. **And: assistant tool file/log reads can be an isolated FS snapshot ≠ the user's live machine — confirm from the user's own shell.**

---

## How we work — non-negotiables

- **The `/security-review` gate is active and mandatory.** Every `git commit` must be preceded by: `git add -A` → a **Sonnet** security review of `git diff --staged` (spawn an `Agent` with `model: "sonnet"`) → resolve findings → `python tools/secreview_approve.py` → ask the user → commit. Run `secreview_approve.py` and `git commit` as **separate** Bash calls (the gate blocks a command that contains `git commit` before the approve in the same call can run). The gate fails closed. See AGENTS.md.
- **Deterministic validation** over screenshot-clicking (see THE VISION). Remote GUI clicking on this multi-monitor setup is unreliable — measure the actual state.
- **Re-verify endpoints live before building.** The API drifts; `/credits` docs and behavior already disagree.
- **Honor the AGENTS.md invariants** — especially: cyclic GC is disabled (don't re-enable; worker-thread-GC-during-paint segfaults), font-metric-driven card geometry, the frameless/never-focused window quirks, and **never name a custom Qt `Property` after a `QWidget` built-in** (`pos`/`size`/`geometry`/… — the v0.8 toggle bug).
- **Make it wild.** If the implementation is the obvious one, you haven't found the feature yet.

---

## What Pulse is (the product)

Source-agnostic Windows tray monitor; a **nav-rail command center** (left icon rail of equal-peer source tabs + a Settings tab, each its own themed panel). Live sources:
- **OpenRouter** — balance gauge, burn-rate forecast, 24h timeline, pinned-model provider-health board **+ The Arena** (benchmark rank crests + Fighter Card).
- **Claude** — 5h/7d/Sonnet usage limits + local 7-day token accounting (JSONL).
- **GPU** — utilization/VRAM/temp/power (NVML). · **System** — CPU/RAM/network (psutil).

Adding a source is uniform — AGENTS.md → "Sources".

## Doc map — what each file is for
| File | Purpose |
|---|---|
| **AGENTS.md** | *How to work here* — Qt/Win32/GC invariants, the Sources contract, the mandatory `/security-review` rule, the validation checklist, API gotchas. Obey it. |
| **docs/OPENROUTER-RESEARCH.md** | The full reverse-engineered OpenRouter API surface (3 tiers). The capability map. |
| **docs/OPENROUTER-ROADMAP.md** | The curated 18-feature build order (3 waves, foundations, dedup ledger). **The build plan.** |
| **docs/LOGGING.md** | The structured-logging system + how to search `%APPDATA%/Pulse/logs/pulse.jsonl`. When the user reports a bug, search the logs. |
| **docs/TESTING.md** | How to validate — pytest + UI recipes. |
| **ROADMAP.md** | Public shipped log + near-term direction. |
| **docs/CLAUDE-LOCAL-DATA.md** | Reverse-engineering of Claude's local data/APIs (incl. the OAuth refresh flow §25). |
| **docs/RESEARCH-2026-06-21.md** | The original broad exploration (all sources/features). Speculative; re-verify. |

## Key decisions (don't relitigate — the *why* is here)
- **The Arena** (`api_client.parse_benchmarks`/`BenchmarkBoard`): `/api/v1/benchmarks` gives ELO but not rank, so we compute global per-category ranks ourselves; tier = best rank-percentile (Bronze→Champion); `tournament_stats` is a *model-level lifetime* medal count (not per-category). Match models by a normalized name key derived from id or display name. Crest band + Fighter Card live in `widgets.PinnedModelCard`; the animated property is `knob` (NOT `pos` — see the invariant).
- **Structured logging** is centralized in `logging_setup.py` (called first in `main`, before Qt). Never `print()` in app code — use `logging.getLogger("pulse.<area>")` and `log.exception()` in `except`. JSON-lines → `%APPDATA%/Pulse/logs/pulse.jsonl`; faulthandler → `pulse-crash.log`. The benign `0x8001010d` COM dump is isolated to the crash log.
- **Claude usage is read-only + rate-limit-resilient.** Reads `~/.claude/.credentials.json` strictly read-only (never refresh/rotate — that can log the user out). 5h/7d bars come from `GET /api/oauth/usage`, which **429-rate-limits** aggressive polling — so the source caches last-good (`sources/claude/usage_store.py`), backs off, stamps an "as of …" recency, and only flags a real 401 as "open Claude Code".
- **Shell = nav-rail + `QStackedWidget`** of per-source `SourcePanel`s; Settings = `settings_panel.SettingsPanel`. Per-source accent is a runtime tween in `theme_controller` (painted widgets read `theme_controller.accent()` + connect its `changed` signal). Motion in `anim.py`. Acrylic was scrapped — each panel paints its own depth gradient + accent glow.
- **Automatic cyclic GC is disabled** (`main.py`); re-enabling reintroduces a worker-thread-GC-during-paint segfault. **Cards/panels are font-metric-driven.** **Sources self-hide** (`is_available()` + `show_*`).

## Known limitation — Claude usage bars
The 5h/7d bars can go stale on **429 rate-limiting** (not token expiry) — handled (cache + backoff + recency). **Residual:** a pure-Desktop user who never runs the `claude` CLI eventually hits a real 401; the designed opt-in auto-refresh (`docs/CLAUDE-LOCAL-DATA.md §25`) would fix it but is low priority (heavy CLI users always have a live session). The 7-day token footer is always live (JSONL, no token).

## Known issue — active rail-slot reads too bright (cosmetic, OPEN)
The **active** nav-rail slot for vivid accents (GPU green / System teal / Settings cyan) looks too bright; OpenRouter (indigo) and Claude (clay) are fine. Dimming the wash alpha / logo opacity did NOT satisfy and was reverted. Likely fix: desaturate/dim the *brand logo* (`assets/logos/*.svg`) for vivid accents, not the wash. **Validate live before claiming fixed** (burned several rounds).

## Transient gotchas / minor polish
- **Dashboard height doesn't shrink per tab.** Switching to a shorter panel (Claude/GPU/System) without re-summoning leaves empty space below the content (the dashboard keeps the taller panel's height). Minor; a real polish item is to size the dashboard to the active panel's content on tab switch.
- The `/security-review` gate activates at session start; if you just changed it, restart.
- Deps: `nvidia-ml-py`, `psutil` (optional, graceful if absent). Logos use `PySide6.QtSvg`.
- `win_backdrop.py` is dormant (acrylic scrapped); safe to delete. The dead `top_provider` field in `ModelInfo` was removed when F1 shipped with #5.
