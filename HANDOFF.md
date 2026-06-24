# HANDOFF.md — start here

**The living "you are here" doc.**
- **New agent?** Read THIS top-to-bottom first, then [AGENTS.md](AGENTS.md). That's the whole onboarding — you'll know the vision, the current state, and exactly what to build next.
- **Outgoing agent?** Update this as your *last act*: Status, what you shipped, what's next, any new decision/gotcha. When something becomes a permanent rule, move it into AGENTS.md. Keep **THE VISION** section stable.

**Last updated:** 2026-06-23 · **v0.8.0 RELEASED** (*The Arena* + logging; pushed/tagged/release w/ `Pulse.exe`). **Since v0.8.0, two unpushed commits on local `main`:** OpenRouter **Wave 1 #2 — "The Ledger"** (foundation **F2** + provider Trust Seals + Custody Dossier + real logos). Not pushed/released yet.

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
- **NEW since v0.8.0 (local `main`, NOT pushed):** OpenRouter **Wave 1 #2 — "The Ledger"** is shipped in two commits:
  - **Foundation F2** (`frontend_client.py`): no-auth client for `openrouter.ai/api/frontend/*` + slug↔permaslug resolver + pure parsers (all-providers, performance, stats/endpoint, uptime-hourly), unit-tested against captured **public** fixtures in `tests/fixtures/`.
  - **The Ledger** (roadmap #2): each provider row on the pinned board wears a painted **Trust Seal** — a shield with a **Custody Score** grade S→F we compute from the provider's data policy + jurisdiction (training on prompts hard-caps to F). Click a seal → a **Custody Dossier** popup (auditable rap sheet that sums to the score + jurisdiction trail + the provider's **real logo** via `logo_store.py`). The (i) popup gained a TRUST column. Opt-out: `show_trust_seals`.
- **Green:** `python -m pytest -q` → **184 passed** (was 124; +60 across F2/Ledger/logos).
- **Wave 1 remaining: #4 Speed Percentile (NEXT), then #3 73-Hour Uptime Ribbon.** F2's `SpeedBoard` + `PermaslugResolver` are already built + tested — #4 wires them in. See below.
- Running `dist/Pulse.exe` is still the v0.8.0 build (rebuild for the new work).

---

## ▶ THE NEXT BUILD — OpenRouter Wave 1 #4: Speed Percentile

**This is queued for you. #2 (The Ledger) is DONE. Do NOT skip ahead to management-key/Analytics features — those come after Wave 1.**

**#4 — Speed Percentile.** Source: `/api/frontend/v1/rankings/performance` (no auth). Per pinned model: where its provider's p50 throughput/latency sits **against the whole fleet** ("faster than 82% of the field"), naming the single best-speed and best-price provider. *Wild seed:* a percentile **ribbon/dial**, not a number — match the bar set by The Arena + The Ledger seals.

**Most of the data layer already exists (F2):**
- `frontend_client.parse_performance` → `SpeedBoard` is built + tested. It exposes `lookup(permaslug)`, `throughput_percentile(permaslug)` and `latency_percentile(permaslug)` (fraction of the field you beat), and each `SpeedRanking` carries `best_throughput_provider`/`_price` + `best_latency_provider`/`_price`.
- `SpeedBoard` is keyed by **permaslug**, so you must resolve the pinned model's public slug → permaslug. `frontend_client.PermaslugResolver` (`parse_catalog_permaslugs`) is built + tested but **not yet wired** — #4 needs to fetch `catalog/models` once and distribute the resolver, OR resolve via it.

**What #4 needs to build (mirror the #2 wiring exactly — it's the template):**
1. Wire two no-auth fetches through `APIWorker` (slot+signal) + `main.py` (slow timer) + `dashboard` (distribute to cards): the **SpeedBoard** (`FrontendClient.get_speed_board`) and the **PermaslugResolver** (`FrontendClient.get_permaslug_resolver`). Gate behind a `show_speed_*` setting.
2. A pure mapping in the card: pinned `model_id` → permaslug (resolver) → `SpeedBoard` percentile + best providers.
3. The **wild render** in `widgets.PinnedModelCard` (font-metric-driven) — a percentile ribbon/dial in the model header area (near the crest), + the best-speed/best-price provider call-out. Consider folding it into the crest/header row or the (i) popup.
4. Deterministic `qapp` test measuring the rendered percentile + a live check.

### Then: #3 — 73-Hour Uptime Ribbon (LAST in Wave 1)
Source: `/api/frontend/v1/stats/endpoint?permaslug=…&variant=standard` → endpoint UUIDs (`parse_endpoint_refs`, built) → `stats/uptime-hourly?id=<UUID>` → `parse_uptime_hourly` → 73 chronological `{date, uptime}` points (built + tested). *Wild seed:* replace the single uptime number with a **GitHub-style 73-cell hourly heat-strip** per provider. **Heads-up:** this is N× requests (1 stats/endpoint + one uptime-hourly per provider per model) — poll sparingly + cache.

### The build flow for each enrichment (non-negotiable)
pure parser unit-tested against a captured sample → render (font-metric-driven; reuse `widgets.py` patterns) → **deterministic validation** (a `qapp` test that measures the result) + a careful live check → `/security-review` → commit. One enrichment, one commit. **The user does the QA/visual validation — keep it fast and precise; don't screenshot-click.**

### Lessons banked from #2 (read these)
- **Re-verify endpoints live first** — `stats/*` 404 on the public slug; they need the versioned permaslug (the whole reason F2's resolver exists).
- **The frontend API bot-blocks the default `python-requests` UA** (connection reset, not 403). `FrontendClient` overrides the UA directly — `requests.Session.headers.setdefault` does NOT work (the session ships a UA already). A deterministic test (`test_frontend_client_overrides_default_user_agent`) locks this; don't regress it. **This bug is invisible to parser tests — only a live boot caught it.**
- **Logos render in the popup only** (a judge panel + the 14px row both rejected logo-on-row as mush). The board hero is the painted seal.
- **The single-instance mutex bites dev loops:** a leaked `python main.py` zombie holds `Global\Pulse_SingleInstance_v1`, and every later boot silently exits "already running" (only logs "logging started"). If a boot logs nothing, kill stray `python.exe …main.py` first (see AGENTS.md "Safe restart").

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
- `win_backdrop.py` is dormant (acrylic scrapped); safe to delete. The `top_provider` field in `ModelInfo` is dead/unused — clean it up when the pricing model gets built (roadmap #5/#6).
