# Pulse

A Windows tray app for monitoring your OpenRouter subscription. Live balance, auto top-up aware forecast, 24h balance timeline, and threshold notifications. Dark themed, frameless, stays out of your way.

More providers and aggregators planned. See [ROADMAP.md](ROADMAP.md).

![dashboard](docs/dashboard.png)

![pinned models](docs/dashboard-pinned.png)

![model picker](docs/dashboard-picker.png)

![dashboard in context](docs/desktop.jpg)

## What it does

- Live balance shown as a circular gauge in the tray icon and a full panel on click.
- Auto top-up aware forecast: tells you when your next top-up will trigger based on your actual burn rate.
- 24 hour balance timeline with top-up jumps marked.
- Today's spend and a 30 day projection.
- Hourly and daily burn rate computed from your own history, persisted across restarts.
- Toast notifications when balance crosses your warning or critical threshold.
- **Pinned models with per-provider health.** Pick the models you actually use, see live p50 latency, 30-min uptime, and price for every provider serving each one. Best provider per model is highlighted. Refreshes every 5 minutes.
- Right-click menu for quick links to OpenRouter's dashboard, credits page, and models page.
- Single instance lock so double-launching is a no-op.

## Install

### Option A: pre-built .exe (no Python needed)

Grab `Pulse.exe` from the [latest release](https://github.com/k4rg1l/pulse/releases/latest), double-click. That's it. First launch puts the tray icon in the hidden-icons area; drag it to the visible tray so it's always there.

Configure your API key once: right-click the tray icon → **Open Settings File...** Add your key in the `api_key` field. Restart Pulse.

The .exe is a self-contained PyInstaller bundle (~50 MB). Some antivirus flags PyInstaller binaries as suspicious on first run — that's a known false positive, you can verify the build is from this repo via the release page.

### Option B: from source (Python 3.10+)

```powershell
git clone https://github.com/k4rg1l/pulse.git
cd pulse
pip install -r requirements.txt
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
python main.py
```

Tray icon appears in the bottom right. Left click opens the dashboard, right click opens the menu.

If you'd rather not use the env var, run the app once to generate `%APPDATA%\Pulse\settings.json`, then put your key in the `api_key` field there. The tray menu has an "Open Settings File..." entry that opens it in your default editor.

### Option C: build your own .exe

```powershell
pip install pyinstaller
python -m PyInstaller pulse.spec --clean --noconfirm
# Output: dist\Pulse.exe
```

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
  "dismiss_on_focus_loss": true,
  "tracked_models": [
    "anthropic/claude-sonnet-4.5",
    "openai/gpt-5",
    "deepseek/deepseek-chat-v3.1",
    "google/gemini-2.5-flash"
  ]
}
```

`tracked_models` is the list of OpenRouter model IDs shown in the "Pinned Models" section. Add or remove freely; the dashboard updates on the next refresh. IDs are the same as the model slugs on openrouter.ai/models.

Set `auto_topup_threshold` and `auto_topup_amount` to whatever you've configured on openrouter.ai. With them set, the forecast switches from "depletes in N days" to "next top-up in N hours" and the gauge shows an indicator.

`management_api_key` is reserved for v0.2 (per-model and per-provider spend). Leave it empty for now.

## What's not in v0.1

This is intentionally minimal. The first release ships the things every OpenRouter user wants on day one. Per-model spend, per-provider health, pinned model watchlists, a cost calculator, daily summary toasts, a global hotkey, and a settings GUI are all planned. See [ROADMAP.md](ROADMAP.md).

## Tech

PySide6, Python 3.10+, requests. About 1.5k lines across 7 files. Pure Qt, no web view, no Electron.

## Contributing

Issues and PRs welcome. If you want to take a roadmap item, open an issue first so we don't duplicate work.

Read [AGENTS.md](AGENTS.md) before touching tray, focus, or window code. It lists the invariants, the validation checklist, and the API gotchas.

## License

MIT. See [LICENSE](LICENSE).
