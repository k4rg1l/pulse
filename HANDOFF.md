# HANDOFF.md — start here

**The living "you are here" doc.**
- **New agent?** Read THIS top-to-bottom first, then [AGENTS.md](AGENTS.md). That's the whole onboarding — you'll know the vision, the current state, and exactly what to build next.
- **Outgoing agent?** Update this as your *last act*: Status, what you shipped, what's next, any new decision/gotcha. When something becomes a permanent rule, move it into AGENTS.md. Keep **THE VISION** section stable.

**Last updated:** 2026-06-23 · **v0.8.0 RELEASED** — *The Arena* + structured logging. Pushed to `origin/main` (github.com/k4rg1l/pulse), tagged `v0.8.0`, GitHub release published **with `Pulse.exe` attached**.

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

## Status — where we are (v0.8.0)

- **Released `v0.8.0`** (`origin/main`, tagged, GitHub release w/ binary): **The Arena** (model rank crests + Fighter Card on the pinned board) and **structured logging**. Plus two fixes (Settings toggle position bug; OpenRouter error path now logs full HTTP detail).
- **Green:** `pip install -r requirements-dev.txt && python -m pytest -q` → **124 passed**.
- **OpenRouter Wave 0 is done** (The Arena = roadmap #1, foundation **F4** Benchmarks client built). Next is **Wave 1** — see below.
- Running `dist/Pulse.exe` is the v0.8.0 build.

---

## ▶ THE NEXT BUILD — OpenRouter Wave 1: the Provider-Intelligence Layer

**This is queued for you. Do NOT skip ahead to management-key/Analytics features — those come after.**

Today each pinned model's provider rows are bare text (name · latency · uptime · price). **Wave 1 transforms them into a rich, trustworthy, beautiful provider view** using OpenRouter's **no-auth `/api/frontend/v1/*` website API** — which works for *every* user, costs zero key budget, and is wide open. It bundles **three roadmap items that all share one client and one board**, so they're built together but **validated one at a time**:

> **Why bundling these three is safe (and only these):** #2, #3, #4 are all no-auth, all enrich the *same* pinned board, and all sit on the *same* frontend client (foundation **F2**). Build F2 once, then add each enrichment and validate it before the next. The user explicitly OK'd a 2–3 feature bundle *only when it's genuinely cohesive* — this is. Do **not** fold in management-key features (Spend X-Ray etc.); those need a different client (F3) and a different auth story.

### Step 0 — Build foundation F2 (the frontend-v1 client + permaslug resolver)
A thin `requests` wrapper for `https://openrouter.ai/api/frontend/v1/*` (plain GET, no auth, no key) + a cached **slug↔permaslug** resolver from `/api/frontend/v1/catalog/models`. **Gotchas (all in OPENROUTER-RESEARCH.md Tier C):** the `/v1/` segment is required; `stats/*` endpoints want the *versioned permaslug* (`anthropic/claude-opus-4.8` → `anthropic/claude-4.8-opus-20260528`), not the public slug; `stats/uptime-hourly` wants `id=<endpoint-UUID>` (from `stats/endpoint`), not a permaslug. Mirror the `api_client.py` pattern (client method + `APIWorker` slot/signal + a slow timer in `main.py`), and re-run `tools/or_probe_frontend.py` to refresh the captured samples your parser unit-tests against.

### The three enrichments (each its own wild treatment + its own validation + commit)
1. **#2 — Provider Logos + Privacy/Trust seal.** Source: `/api/frontend/all-providers` (no auth, no permaslug needed — the most self-contained, ship it FIRST). Each provider gets its **real logo** (`icon.url`) and a glanceable **trust seal** computed from `dataPolicy{training, retainsPrompts, retentionDays, canPublish}` + jurisdiction (`headquarters` + `datacenters` country). *Wild seed:* a computed trust **grade** (e.g. 🛡️ "zero-retention" → ⚠️ "trains on prompts · 30-day") or a wax-seal/clearance-badge, expanding to a "provider dossier" (data policy + datacenter flags). Logos alone make the board look 10× more premium.
2. **#4 — Speed Percentile.** Source: `/api/frontend/v1/rankings/performance` (no auth). Per pinned model: where its provider's p50 throughput/latency sits **against the whole fleet** ("faster than 82% of the field"), naming the single best-speed and best-price provider (`best_throughput_provider`/`_price`, `best_latency_provider`/`_price`). *Wild seed:* a percentile ribbon/dial, not a number.
3. **#3 — 73-Hour Uptime Ribbon.** Source: `/api/frontend/v1/stats/endpoint?permaslug=…&variant=standard` → get endpoint UUIDs → `stats/uptime-hourly?id=<UUID>` → `data.history[]` of 73 hourly `{date, uptime}` points (no auth). *Wild seed:* replace the single 30m/5m/1d uptime number with a **GitHub-style 73-cell hourly heat-strip** per provider — see the exact hour a provider had an outage.

**If the bundle starts to feel heavy, ship #2 alone first and validate** — it's the most self-contained and the most visually transformative. Then #4, then #3. Small, validated steps win.

### The build flow for each enrichment (non-negotiable)
pure parser unit-tested against a captured sample → render (font-metric-driven; reuse `widgets.py` patterns) → **deterministic validation** (a `qapp` test that measures the result) + a careful live check → `/security-review` → commit. One enrichment, one commit.

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
