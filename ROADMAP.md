# Roadmap

What's coming after v0.1. Order is rough, not strict. Each item links back to why it's worth doing.

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

## Next (v0.2)

**Per-model and per-provider spend.** OpenRouter's `/api/v1/activity` is locked behind management keys. The user creates one at openrouter.ai/settings/keys and pastes it into settings.json. We then unlock:

- Spend by model (this month, this week, today)
- Spend by provider
- Top N models by cost
- Last N generations feed (timestamp, model, cost, latency)

**Cleanup that didn't make v0.1**

- Settings GUI dialog (right now you edit JSON in Notepad)
- Move API key out of plain text in settings.json into Windows Credential Manager
- Pre-built `.exe` distributed through GitHub Releases (PyInstaller)

## Soon (v0.3)

**Per-provider health, done right.** The current `/api/v1/models/{author}/{slug}/endpoints` endpoint exposes uptime (5m, 30m, 1d), p50 to p99 latency, throughput, and quantization per provider serving a model. With management-key activity data, we know exactly which models the user actually hits. Combining these:

- A watchlist of models the user pins (or auto-pinned from their top 5 by spend)
- For each, a live heatmap of providers: green dot for high uptime, color graded down, hover for p90 latency and throughput
- Click into a model to see all providers ranked
- Outage detection: if a provider serving a pinned model drops below an uptime threshold, fire a toast

This replaces the deleted Service Status section with something actually actionable.

**Models, reinvented.** Don't replicate openrouter.ai/models. Be useful:

- Pinned models list with live price and best-provider uptime in one row each
- Cost calculator widget: pick a model, enter prompt token count and completion token count, get a cost in dollars with the cheapest provider
- Smart picks card: "cheapest in chat", "cheapest in vision", "best uptime today"

## Later (v0.4 and on)

**Notifications, done right**

- Top-up triggered (when we detect a balance jump)
- Running out (already have warning and critical thresholds)
- Provider outage on a pinned model
- Daily summary toast on first open of a new day, summarizing the previous day's spend

**Top-up history view**

- Surface every detected top-up from snapshot history
- Total auto-top-ups this month as a small KPI tile

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
