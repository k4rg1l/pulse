# HANDOFF.md — start here

**The living "you are here" doc.**
- **New agent?** Read THIS first, then [AGENTS.md](AGENTS.md), then continue. That's the whole onboarding.
- **Outgoing agent?** Update this as your *last act*: Status, what you changed, what's next, any new decision/gotcha. Keep it short and current. When something here becomes a permanent rule, move it into AGENTS.md and delete it here.

**Last updated:** 2026-06-22 · **v0.7.0 RELEASED** — command-center UI overhaul + Settings tab pushed to `origin/main` (github.com/k4rg1l/pulse), tagged `v0.7.0`, GitHub release published.

---

## Status
- **Released `v0.7.0`** (HEAD `a0d4395`, in sync with `origin/main`, tagged + GitHub release): the **nav-rail "command center" UI overhaul** — a left icon rail of equal-peer source tabs (OpenRouter / Claude / GPU / System) plus a Pulse-cyan **Settings tab**, each with its own brand logo, accent identity, themed panel + hued background, live status dots, and animated tab transitions. **UI-only — the same per-source data as before.** Also in this release: the mandatory `/security-review` commit gate and the rate-limit-resilient Claude usage fix (cache + backoff + recency stamp).
- **Green:** `pip install -r requirements-dev.txt && python -m pytest -q` → **112 passed**.
- ⚠ **The v0.7.0 GitHub release has NO built `Pulse.exe` attached** — only source + tag (the v0.6.0 release shipped a binary). To ship it: `pyinstaller pulse.spec` then `gh release upload v0.7.0 dist/Pulse.exe`.
- **Next (the real prize):** go **DEEPER per source** — data was intentionally left unchanged during the UI overhaul; the roomy panels now have space for it. See Next steps.

## Known issue — active rail-slot reads too bright (cosmetic, OPEN)
The **active** nav-rail slot for the *vivid* accents (GPU green / System teal / Settings cyan) looks too bright to the user; OpenRouter (indigo) and Claude (clay) are fine. Dim attempts — lowering the active wash alpha (`int(28 + 16*glow)` in `nav_rail._paint_slot` / `_paint_settings`) and the active logo opacity — did NOT satisfy and were **reverted to the original** at the user's request, so the shipped state is the original (brighter) rendering. Likely culprit is the **full-opacity brand logo** (`assets/logos/*.svg`) more than the wash — a real fix probably desaturates/dims the *logo* for vivid accents, not the wash. **Validate live before claiming it's fixed** (this burned several rounds).

## What Pulse is now
A **source-agnostic** Windows tray monitor. The dashboard is a **nav-rail command center**: a left icon rail of equal-peer source tabs, each opening a roomy themed panel. No provider is privileged. Live sources:
- **OpenRouter** — balance, burn-rate forecast, 24h timeline, pinned-model provider health.
- **Claude** — 5h/7d/Sonnet usage limits + local 7-day token accounting (from JSONL).
- **GPU** — utilization/VRAM/temp/power (NVML).
- **System** — CPU/RAM/network (psutil).

Adding a source is uniform — see **AGENTS.md → "Sources"** for the contract + a how-to.

## Read next (doc map — what each file is for)
| File | Purpose |
|---|---|
| **AGENTS.md** | *How to work here* — invariants (Qt/Win32/**GC**), the Sources contract, the mandatory `/security-review` rule, the 20-point validation list, API gotchas. Stable; obey it. |
| **docs/TESTING.md** | *How to validate* — pytest + Windows-MCP UI recipes + automation gotchas. Read before validating. |
| **ROADMAP.md** | *Where we're going* — shipped + next candidates. |
| **docs/RESEARCH-2026-06-21.md** | The big exploration. **Speculative — a map, not a spec.** Re-verify any endpoint before building. |
| **docs/CLAUDE-LOCAL-DATA.md** | Reverse-engineering of Claude's local data/APIs (basis for the Claude source; incl. the OAuth refresh flow in §25). |

## Key decisions (don't relitigate — the *why* is here)
- **Claude usage is read-only + rate-limit-resilient.** Pulse reads `~/.claude/.credentials.json` strictly read-only (never refresh/rotate — rotation can break the user's Claude login; see `sources/claude/credentials.py`). The 5h/7d bars come from `GET /api/oauth/usage`, which **429-rate-limits** aggressive polling — so the source caches last-good (persisted, `sources/claude/usage_store.py`), backs off, stamps an "as of …" recency, and only flags a *real* 401 as "open Claude Code". See Known limitation.
- **The shell is a nav-rail + `QStackedWidget` of per-source panels** (the v0.7 overhaul). Tabs register via `Dashboard.register_source_tab` (OpenRouter via `_register_openrouter`); rail = `nav_rail.NavRail`, panel = `source_panel.SourcePanel`, Settings tab = `settings_panel.SettingsPanel`. Per-source accent identity is a runtime tween in `theme_controller` (painted widgets read `theme_controller.accent()` and connect its `changed` signal to `update()`); severity → rail status dots via the new `Source.severity(data)`. Motion helpers in `anim.py`. **Acrylic was scrapped** (it didn't engage on the frameless / never-focused window and flattened the look) → each panel paints its own depth gradient + per-source accent glow instead.
- **Automatic cyclic GC is disabled** (`main.py` `gc.disable()` + a main-thread `gc.collect()` timer). Re-enabling it reintroduces a worker-thread-GC-during-paint **segfault** (reproduced + fixed). See the AGENTS.md invariant.
- **Cards/panels are font-metric-driven** (one `_build_ops()` feeds both paint and height) so content never clips. Don't hardcode heights.
- **Sources self-hide** (`is_available()` + a `show_*` setting) and degrade gracefully when data/creds are absent.

## Known limitation — Claude usage bars
The 5h/7d **utilization bars** come from `GET /api/oauth/usage`. **Diagnosed this session:** the usual staleness was NOT token expiry — it was **HTTP 429 rate-limiting** of that endpoint (the token was valid, ~7h left). Shipped fix (`sources/claude/`): classify the fetch (429 vs 401 vs network), **cache last-good + persist it** (`usage_store.py`), **back off** on 429, stamp an **"as of …" recency**, and only say "open Claude Code" on a real 401 — so the bars degrade gracefully instead of blanking. **Residual:** a pure-Desktop user who never runs the `claude` CLI will eventually hit a real 401 — the only case the designed opt-in auto-refresh would help, and it's lower priority now (heavy CLI users always have a live session, so the no-live-session refresh guard would rarely fire). The 7-day token footer is always live (JSONL, no token).

## Next steps (prioritized)
1. **Go DEEPER per source (the real prize).** The overhaul deliberately kept the same data; the roomy panels now have space. Already-available, high-value data: **Claude** active-sessions / "what it's working on" / full `by_model` breakdown / web-searches; **OpenRouter** provider-health board (latency p50/p90, throughput, uptime) / daily-spend timeline / depletion ETA / top-up history; **GPU** clocks / fan / per-process; **System** per-core CPU / disk / uptime / battery / net sparklines; **History** day/hour heatmaps + anomaly detection. (Each source's `poll()` data object is where to extend; the panel bodies in `sources/*/card.py` are where to render.)
2. **Fix the active rail-slot brightness** — see "Known issue" above. Cosmetic but the user cares, and my attempts were reverted; do it properly (likely dim/desaturate the *logo* for vivid accents) and validate live.
3. **[Designed, lower priority now] Opt-in Claude token auto-refresh** — only helps the pure-Desktop 401 case (see Known limitation). Shape: setting `claude_auto_refresh: bool = False`; refresh ONLY when no live `claude` session (check `~/.claude/sessions/*.json` for a live PID — note stale files linger, so verify the PID is alive AND really claude); trigger on the Refresh click + background poll when enabled/expired/no-session; mechanism = POST `grant_type=refresh_token` to `https://platform.claude.com/v1/oauth/token` (client_id `9d1c250a-e61b-44d9-88ed-5944d1962f5e`) then **atomic write** under a **file lock** after backing up the file. Full flow in `docs/CLAUDE-LOCAL-DATA.md` §25.
4. **MCP server** (read-only first) — let Claude Code query Pulse ("what's my balance/burn rate?"). The keystone in the research doc.
5. **Notifications / alert engine + AppUserModelID** — toasts should read "Pulse" not "Python"; add top-up / daily-summary / limit alerts. (A basic threshold toast already exists in `tray_icon.py`.)

## Transient gotchas (not permanent enough for AGENTS.md yet)
- Dev runs via `pythonw.exe` put the tray icon in the hidden-icons overflow (Windows keys placement by exe path); the released `.exe` / the user's normal launch are unaffected.
- Deps: `nvidia-ml-py`, `psutil` (both optional, graceful if absent). Logos use `PySide6.QtSvg` (ships with PySide6).
- `win_backdrop.py` exists but is **dormant** (acrylic was scrapped). Safe to delete if you want; left in case a future attempt wants a starting point.
- The `/security-review` gate is **active** — every `git commit` must be preceded by a Sonnet security review + `python tools/secreview_approve.py`. See AGENTS.md.
