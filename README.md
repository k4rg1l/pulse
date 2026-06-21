# Pulse

A Windows tray app for monitoring your OpenRouter subscription. Live balance, auto top-up aware forecast, 24h balance timeline, and threshold notifications. Dark themed, frameless, stays out of your way.

More providers and aggregators planned. See [ROADMAP.md](ROADMAP.md).

![dashboard](docs/dashboard.png)

![dashboard in context](docs/desktop.jpg)

## What it does

- Live balance shown as a circular gauge in the tray icon and a full panel on click.
- Auto top-up aware forecast: tells you when your next top-up will trigger based on your actual burn rate.
- 24 hour balance timeline with top-up jumps marked.
- Today's spend and a 30 day projection.
- Hourly and daily burn rate computed from your own history, persisted across restarts.
- Toast notifications when balance crosses your warning or critical threshold.
- Right-click menu for quick links to OpenRouter's dashboard, credits page, and models page.
- Single instance lock so double-launching is a no-op.

## Install

Requires Python 3.10 or newer and `pip`. PySide6 ships its own Qt runtime so there's nothing else to install.

```powershell
git clone https://github.com/k4rg1l/pulse.git
cd pulse
pip install -r requirements.txt
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
python main.py
```

That's it. You'll see a tray icon in the bottom right. Left click opens the dashboard, right click opens the menu.

If you'd rather not use the env var, run the app once to generate `%APPDATA%\Pulse\settings.json`, then put your key in the `api_key` field there. The tray menu has an "Open Settings File..." entry that opens it in your default editor.

## Run on startup

Right-click the tray icon and tick "Start with Windows". This adds a registry entry under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` that points at your current Python and main.py.

## Configure

All settings live in `%APPDATA%\Pulse\settings.json`:

```json
{
  "api_key": "sk-or-v1-...",
  "management_api_key": "",
  "auto_topup_threshold": 2,
  "auto_topup_amount": 25,
  "balance_warning": 5,
  "balance_critical": 1,
  "key_refresh_seconds": 60,
  "dismiss_on_focus_loss": true
}
```

Set `auto_topup_threshold` and `auto_topup_amount` to whatever you've configured on openrouter.ai. With them set, the forecast switches from "depletes in N days" to "next top-up in N hours" and the gauge shows an indicator.

`management_api_key` is reserved for v0.2 (per-model and per-provider spend). Leave it empty for now.

## What's not in v0.1

This is intentionally minimal. The first release ships the things every OpenRouter user wants on day one. Per-model spend, per-provider health, pinned model watchlists, a cost calculator, daily summary toasts, a global hotkey, and a settings GUI are all planned. See [ROADMAP.md](ROADMAP.md).

## Tech

PySide6, Python 3.10+, requests. About 1.5k lines across 7 files. Pure Qt, no web view, no Electron.

## Contributing

Issues and PRs welcome. If you want to take a roadmap item, open an issue first so we don't duplicate work.

Read [docs/AGENT_NOTES.md](docs/AGENT_NOTES.md) before touching tray, focus, or window code. It captures the non-obvious things we learned the hard way (frameless window quirks, why click-outside dismiss uses polling, the taskbar-entry fix, the safe restart command, the OpenRouter API constraints). Saves a couple of debugging cycles for anyone new.

## License

MIT. See [LICENSE](LICENSE).
