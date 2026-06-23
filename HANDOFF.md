# HANDOFF.md — start here

**The living "you are here" doc.**
- **New agent?** Read THIS first, then [AGENTS.md](AGENTS.md), then continue. That's the whole onboarding.
- **Outgoing agent?** Update this as your *last act*: Status, what you changed, what's next, any new decision/gotcha. Keep it short and current. When something here becomes a permanent rule, move it into AGENTS.md and delete it here.

**Last updated:** 2026-06-22 · by the agent that shipped the v0.7.0 command-center UI overhaul + Settings tab.

---

## Status
- **Shipped on `main`:** the **nav-rail "command center" UI overhaul** — a left icon rail of equal-peer source tabs (OpenRouter / Claude / GPU / System) plus a Pulse-cyan **Settings tab**, each with its own brand logo, accent identity, themed panel + hued background, live status dots, and animated tab transitions. **UI-only — the same per-source data as before.** Also shipped earlier: the mandatory `/security-review` commit gate and the rate-limit-resilient Claude usage fix (cache + backoff + recency stamp).
- **Green:** `pip install -r requirements-dev.txt && python -m pytest -q` → 112 passed.
- **Next (the real prize):** go **DEEPER per source** — the data was intentionally left unchanged during the UI overhaul. The roomy panels now have space for it (Claude active-sessions / model breakdown, OpenRouter provider health board, GPU clocks/fan, System per-core/disk/uptime, History heatmaps). See Next steps.

## What Pulse is now
A **source-agnostic** Windows tray monitor. The dashboard is a *neutral host* that renders peer "source" section-groups in `settings.source_order` — no provider is privileged. Live sources:
- **OpenRouter** — balance, burn-rate forecast, 24h timeline, pinned-model provider health.
- **Claude** — 5h/7d/Sonnet usage limits + local 7-day token accounting (from JSONL).
- **GPU** — utilization/VRAM/temp/power (NVML).
- **System** — CPU/RAM/network (psutil).

Adding a source is uniform — see **AGENTS.md → "Sources"** for the contract + a how-to.

## Read next (doc map — what each file is for)
| File | Purpose |
|---|---|
| **AGENTS.md** | *How to work here* — invariants (Qt/Win32/**GC**), the Sources contract, the 20-point validation list, API gotchas. Stable; obey it. |
| **docs/TESTING.md** | *How to validate* — pytest + Windows-MCP UI recipes + automation gotchas. Read before validating. |
| **ROADMAP.md** | *Where we're going* — shipped + next candidates. |
| **docs/RESEARCH-2026-06-21.md** | The big exploration. **Speculative — a map, not a spec.** Re-verify any endpoint before building. |
| **docs/CLAUDE-LOCAL-DATA.md** | Reverse-engineering of Claude's local data/APIs (basis for the Claude source; incl. the OAuth refresh flow in §25). |

## Key decisions (don't relitigate — the *why* is here)
- **Claude token is read-only by default.** Pulse reads `~/.claude/.credentials.json`; it must NOT refresh/rotate it unless the user opts in (rotation can break their Claude login). See `sources/claude/credentials.py`. (Opt-in refresh is designed — see Next steps #1.)
- **Automatic cyclic GC is disabled** (`main.py` `gc.disable()` + a main-thread `gc.collect()` timer). Re-enabling it reintroduces a worker-thread-GC-during-paint **segfault** (reproduced + fixed). See the AGENTS.md invariant.
- **OpenRouter is a peer source, not the host** (the agnostic migration). Sources mount via `Dashboard.mount_source`.
- **Cards are font-metric-driven** (one `_build_ops()` feeds both paint and height) so content never clips. Don't hardcode card heights.
- **Sources self-hide** (`is_available()` + a `show_*` setting) and degrade gracefully when data/creds are absent.

## Known limitation — Claude usage bars (real task to pick up)
The 5h/7d **utilization bars** read `~/.claude/.credentials.json`, which is **only refreshed by the terminal `claude` CLI**. The Claude **Desktop** app authenticates from its own separate (encrypted) store and does **not** update that file — so for Desktop-only users the bars sit "stale" until they run `claude` in a terminal once. The **7-day token accounting is always live** (parsed from JSONL; no token needed). Worth closing (many users are terminal-first, but not all).

## Next steps (prioritized)
1. **[Designed, ready to build] Opt-in Claude token auto-refresh** — makes the usage bars work for Desktop users. User-approved shape (refine with them):
   - Setting `claude_auto_refresh: bool = False` (explicit, minimal opt-in toggle).
   - **Refresh only when NO `claude` session is alive** (check `~/.claude/sessions/*.json` for a live PID). If a session is running, its token is healthy — leave it alone.
   - Trigger on (a) the dashboard Refresh click and (b) the background poll — only when enabled, token expired/expiring, and no live session.
   - Mechanism: POST `grant_type=refresh_token` to `https://platform.claude.com/v1/oauth/token` (client_id `9d1c250a-e61b-44d9-88ed-5944d1962f5e`), then **atomic write** (temp+replace) the rotated tokens back, under a **file lock**, after backing up the file. Full flow in `docs/CLAUDE-LOCAL-DATA.md` §25.
   - Safe because the Desktop session uses a *separate* token store (verified) — refreshing the CLI file won't log the user out of Desktop. The only race (a running CLI refreshing at the same instant) is covered by the no-live-session guard + lock.
2. **MCP server** (read-only first) — let Claude Code query Pulse ("what's my balance/burn rate?"). The keystone in the research doc.
3. **Notifications / alert engine + AppUserModelID** — toasts should read "Pulse" not "Python"; add top-up / daily-summary / limit alerts. (A basic threshold toast already exists in `tray_icon.py`.)
4. **20-point checklist pass on v0.6.0** (the user is having an agent do this) — esp. the pinned-models invariants after the agnostic re-parenting.
5. (User flagged a separate design concern with the current dashboard layout — ask them.)

## Transient gotchas (not permanent enough for AGENTS.md yet)
- Dev runs via `pythonw.exe` put the tray icon in the hidden-icons overflow (Windows keys placement by exe path); the released `.exe` / the user's normal launch are unaffected.
- New deps as of v0.6: `nvidia-ml-py`, `psutil` (both optional, graceful if absent).
