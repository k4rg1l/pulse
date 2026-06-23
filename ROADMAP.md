# Roadmap

What's coming after the current release. Order is rough, not strict. Each item links back to why it's worth doing.

Pulse is a **source-agnostic** monitor. It shows OpenRouter, Claude, GPU, and System as **peer** sources (no provider is privileged); more can be added on the same contract — see AGENTS.md → "Sources".

## Shipped

### v0.1
- Tray icon with live balance gauge and rich tooltip
- Frameless dark dashboard popup anchored to the tray
- Auto top-up aware burn rate forecast with explanation tooltip
- 24h balance timeline (sourced from local snapshot history)
- Today's spend and 30-day projection KPIs
- Threshold based toast notifications
- Quick links and right-click menu
- Persistence: snapshots, 90 day retention, top-up event detection
- Single instance lock, click-outside dismiss, no taskbar entry

### v0.2
- Pinned Models section with per-provider health
- Per provider: live p50 latency, 30-min uptime, prompt/completion price
- Best provider auto-highlighted (lowest latency among uptime ≥99%)
- 5-minute refresh cadence
- Column headers above cards (PROVIDER / LAT / UP% / $/M IN·OUT)

### v0.3
- Dynamic model picker inside the Pinned Models section. Search bar opens a list of every model in OpenRouter's catalog; click a star to toggle pin. Saves to settings.json instantly.
- X clear button on the search bar; search text persists across close+reopen
- PyInstaller .exe attached to GitHub Releases. No Python install needed.
- Crash-log redirect for windowed PyInstaller builds (`%APPDATA%/Pulse/pulse.log`)

### v0.4
- Collapsible Pinned Models section (▾/▸ chevron)
- Click-to-toggle provider info popup with (i) icon on each card. Floats outside the dashboard so it doesn't cover the cards.
- Picker dropdown now overlays the cards instead of replacing them. Cards stay visible underneath.
- Dashboard always opens at the top (auto-resets scroll on show)
- Picker and info popup auto-dismiss when the dashboard scrolls
- Header layout fixed so long model names elide cleanly without overlapping the ★ Provider chip
- Tooltip rewrites: HTML, sectioned, less jargon

### v0.5
- **Pulse is now multi-source.** New `sources/` architecture: each source (OpenRouter,
  Claude, …) is a self-contained, pollable, self-rendering peer — `is_available()` /
  `poll()` (worker thread) / `build_card()` (main thread) — so no single provider is
  privileged. OpenRouter migrates onto this contract incrementally (the full card refactor).
- **Claude source** (auto-detected when `~/.claude/.credentials.json` exists; hide with
  `show_claude: false`):
  - 5h / 7d / Sonnet usage utilisation with reset countdowns and severity colour, from the
    consumer-plan `/api/oauth/usage` endpoint. **Strictly read-only** — Pulse never
    refreshes or rotates the Claude token (Claude Code owns that lifecycle; rotating it
    could log the user out). Expired/unreachable → degrades to a "stale" state.
  - Local 7-day token accounting from `~/.claude/projects/**/*.jsonl` (total tokens, cache
    efficiency, message count, per-model split), with a per-file mtime/size cache so large
    transcripts aren't re-parsed each poll.
- **Testing foundation:** a `pytest` suite for the pure logic (persistence, settings,
  api_client, Claude parsers) plus **[docs/TESTING.md](docs/TESTING.md)** — the standard for
  how to test (automated + Windows-MCP UI recipes + every automation gotcha). Read it before
  validating; expand it when you ship a feature.
- **Section-title alignment fix:** section headers and the pinned column labels now sit flush
  with the card borders.

Direction (agreed): make Pulse a true multi-source monitor — the OpenRouter→Source migration
(so no provider is privileged), then notifications/alert engine, daily-spend polish, GPU/system
sources, and an MCP server. See `docs/RESEARCH-2026-06-21.md` for the full exploration.

### v0.6

- **Source-agnostic dashboard.** OpenRouter is no longer the host — the dashboard
  is a neutral ordered section host (`settings.source_order`), and OpenRouter,
  Claude, GPU, and System render as peer section-groups. Adding a source is
  uniform; no provider is privileged. (See AGENTS.md → "Sources".)
- **NVIDIA GPU source** — utilization / VRAM / temperature / power via NVML
  (`nvidia-ml-py`); auto-detected, hidden on non-NVIDIA machines.
- **System vitals source** — CPU / RAM / network up-down via `psutil`.
- **Global hotkey** — Win+Shift+O (configurable; `settings.hotkey`) summons the
  dashboard, via Win32 `RegisterHotKey` (no AV-tripping low-level hook).
- 82 unit tests; every new source's parser is unit-tested + its card render-tested.

### v0.7

- **Nav-rail command center.** The dashboard became a left icon rail of equal-peer source tabs (OpenRouter / Claude / GPU / System) plus a Settings tab — each with its own brand logo, accent identity, themed panel, live status dots, and animated transitions.
- **Settings tab** — toggle sources, pick the default panel, and set animation / click-away behavior without editing JSON.
- Rate-limit-resilient Claude usage (cache + backoff + "as of …" recency) and a mandatory pre-commit `/security-review` gate.

### v0.8

- **The Arena.** Pinned models gain a competitive rank crest from their DesignArena ELO (Bronze → Champion) with globally-computed ranks, plus a click-through Fighter Card (Artificial Analysis intelligence/coding/agentic indices, lifetime tournament medals, full category ladder). Built on `/api/v1/benchmarks`.
- **Structured logging** — JSON-lines to `%APPDATA%/Pulse/logs/` (searchable, rotating) with uncaught-exception and Qt-message capture, plus a separate crash log. See [docs/LOGGING.md](docs/LOGGING.md).
- Fixes: Settings toggle position bug; OpenRouter error path now logs full HTTP detail.

## Next

The near-term focus is **going deeper per source**, starting with OpenRouter. A full reverse-engineering of its API and a curated, deduped build order live in **[docs/OPENROUTER-ROADMAP.md](docs/OPENROUTER-ROADMAP.md)** (backed by [docs/OPENROUTER-RESEARCH.md](docs/OPENROUTER-RESEARCH.md)) — real per-model / per-provider spend attribution, cheapest-provider routing, quality-per-dollar, uptime history, and provider privacy badges.

Standing candidates, any time:

- **Notifications, done right** — top-up triggered, running low, provider outage on a pinned model, a daily-summary toast.
- **Cost calculator** — pick a model, enter token counts, get the cost across every provider serving it (data already fetched for the health board).

## Soon (v0.7+)

**Per-model and per-provider spend.** OpenRouter's `/api/v1/activity` is locked behind management keys. When the user has a management key set (`management_api_key` in settings.json) and has accumulated enough org-scoped usage, unlock:

- Spend by model (this month, this week, today)
- Spend by provider
- Top N models by cost
- Last N generations feed (timestamp, model, cost, latency)

The schema is reverse-engineered; the activity endpoint just needs real data to show up. Current blocker is that the user's day-to-day OpenRouter usage hasn't been routed through the org long enough for `/activity` to return populated rows.

## Watching

Things we'd love to add but the platform doesn't expose what we'd need yet.

- **Claude Max subscription tracking.** Flat-rate consumer plan, no programmatic usage API today. If Anthropic ever exposes Max usage via the Console API, this becomes a second tab with the same shape as the OpenRouter one. Until then, no integration path that doesn't involve fragile auth-cookie scraping.
- **Anthropic Console / OpenAI / Bedrock spend tabs.** All require admin-scoped credentials that change the UX significantly. Worth doing when we have the abstraction shape proven out by per-model spend work above.

## Distribution

- ✅ Pre-built `.exe` via PyInstaller, attached to each GitHub Release. (Shipped in v0.3.)
- PyPI package so `pipx install pulse-tray` and `pulse` works
- GitHub Actions for lint on every push
- Issue templates for bug reports and feature requests
- Contributor guide

## Maybe

Ideas that need a clear use case before they're worth building.

- Generation lookup: paste a generation ID, get full breakdown. Niche.
- Budget mode: set a monthly $ cap, show progress and projected overage. Useful if users actually have budgets.
- Provider routing visualizer: where each request went. Cool but unclear if actionable.
- Cookie-session scrape of openrouter.ai/activity for users who don't want a management key. Fragile; skip unless many users ask.

## Not doing

- Browsing the full 340 model catalog in a giant grid. openrouter.ai/models already does this better. The dynamic picker covers the find-and-pin use case.
- Service health summary as 3 generic dots. Useless without per-provider granularity. (Removed in v0.1.)
- "Updated 35s ago" label. Nobody is watching the clock. (Removed in v0.1.)
- Web view or Electron. Native PySide6 stays.
