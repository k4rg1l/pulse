# Roadmap

What's coming after v0.1. Order is rough, not strict. Each item links back to why it's worth doing.

The app is called Pulse. v0.1 supports OpenRouter. The name leaves room to add more providers and aggregators later (Anthropic console, OpenAI usage, AWS Bedrock spend, etc.) without a rename. Cross-provider unification is a long-term goal, not a v0.2 promise.

## Now (v0.1, shipped)

- Tray icon with live balance gauge and rich tooltip
- Frameless dark dashboard popup anchored to the tray
- Auto top-up aware burn rate forecast with explanation tooltip
- 24h balance timeline (sourced from local snapshot history)
- Today's spend and 30-day projection KPIs
- Threshold based toast notifications
- Quick links and right-click menu
- Persistence: snapshots, 90 day retention, top-up event detection
- Single instance lock, click-outside dismiss, no taskbar entry

## Next (v0.2, building now)

**Pinned models with per-provider health.** Uses `/api/v1/models/{author}/{slug}/endpoints` (untapped today). For each model you pin in settings, show every provider serving it with p50 latency, 30-min uptime, and price side by side. Mark the best-overall provider per model. 5-minute background refresh. Replaces the deleted Service Status section with something actionable.

- New `tracked_models` field in settings.json (list of OpenRouter model IDs)
- New PINNED MODELS section in the dashboard
- Per-provider rows with metric pills and best-of indicator
- Tooltip on each row with full numbers (throughput, p90 latency, quantization)
- Auto-refresh every 5 min; manual refresh on dashboard refresh

## Soon (v0.3)

**Per-model and per-provider spend** via the management key in settings. Currently blocked by `/api/v1/activity` only seeing activity from org-scoped keys. Once enough org-scoped usage accumulates:

- Spend by model (this month, this week, today)
- Spend by provider
- Top N models by cost
- Last N generations feed (timestamp, model, cost, latency)

**Cleanup that didn't make v0.1**

- Settings GUI dialog (right now you edit JSON in Notepad)
- Move API key out of plain text in settings.json into Windows Credential Manager
- Pre-built `.exe` distributed through GitHub Releases (PyInstaller)

## Distribution

- Pre-built `.exe` via PyInstaller, attached to each GitHub Release. Removes the Python + pip + git clone install step entirely. Tracked separately because PyInstaller builds need iteration to get clean (no false positives from Windows Defender, bundled Qt plugins).

## Later (v0.4 and on)

**Notifications, done right**

- Top-up triggered (when we detect a balance jump)
- Running out (already have warning and critical thresholds)
- Provider outage on a pinned model
- Daily summary toast on first open of a new day, summarizing the previous day's spend

**Top-up history view**

- Surface every detected top-up from snapshot history
- Total auto-top-ups this month as a small KPI tile

**Cost calculator**

- Pick a model, enter prompt token count and completion token count, get a cost in dollars with the cheapest provider highlighted
- Quick "what would this prompt cost across my pinned models" comparison

**Smart picks card**

- "cheapest in chat", "cheapest in vision", "best uptime today" auto-derived from the live `/endpoints` data

**UX polish**

- Global hotkey to open the dashboard (default Win+Shift+O, configurable)
- Drag to reposition the dashboard anywhere on screen
- Theme variants: current dark, minimal, OLED black
- Compact mode (gauge plus burn rate only, hide everything else)

**Open source plumbing**

- PyPI package so `pipx install openrouter-pulse` and `pulse` works
- GitHub Actions for lint on every push
- Issue templates for bug reports and feature requests
- Contributor guide

## Watching

Things we'd love to add but the platform doesn't expose what we'd need yet.

- **Claude Max subscription tracking.** Flat-rate consumer plan, no programmatic usage API today. If Anthropic ever exposes Max usage via the Console API, this becomes a second tab with the same shape as the OpenRouter one. Until then, no integration path that doesn't involve fragile auth-cookie scraping.
- **Anthropic Console / OpenAI / Bedrock spend tabs.** All require admin-scoped credentials that change the UX significantly. Worth doing when we have the abstraction shape proven out by v0.3's per-model spend work.

## Maybe

Ideas that need a clear use case before they're worth building.

- Generation lookup: paste a generation ID, get full breakdown. Niche.
- Budget mode: set a monthly $ cap, show progress and projected overage. Useful if users actually have budgets.
- Provider routing visualizer: where each request went. Cool but unclear if actionable.
- Cookie-session scrape of openrouter.ai/activity for users who don't want a management key. Fragile; skip unless many users ask.

## Not doing

- Browsing the full 340 model catalog. openrouter.ai/models already does this better.
- Service health summary as 3 generic dots. Useless without per-provider granularity.
- "Updated 35s ago" label. Nobody is watching the clock.
- Web view or Electron. Native PySide6 stays.
