# AGENTS.md

For anyone (or anything) editing this repo.

## Run

```powershell
pip install -r requirements.txt
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
python main.py
```

## Validate before merging

Open the dashboard and check every item. Skip none.

1. App starts with no exceptions.
2. Tray icon visible, tooltip shows the balance.
3. Left-click the tray opens the dashboard.
4. Right-click the tray shows the FULL menu (not truncated under the taskbar).
5. Clicking INSIDE the dashboard does NOT close it.
6. Clicking OUTSIDE the dashboard DOES close it.
7. The refresh button refetches.
8. No phantom "Python" entry appears in the taskbar when the dashboard opens.
9. "Open Settings File..." opens settings.json in the default editor.
10. The Exit menu actually quits the process.
11. State persists across a restart (settings and snapshot history).

## Invariants

These exist because we hit them the hard way. Breaking any one corrupts behavior in non-obvious ways.

**`OpenRouterPulse` must inherit `QObject`.** Cross-thread signals from the worker only marshal into the main event loop when the receiver is a `QObject`. A plain Python class corrupts the GUI heap. `super().__init__()` must run after `QApplication` exists.

**Never `setWindowTitle()` on the dashboard.** Setting a title produces a brief ghost window with a native frame before Qt applies `FramelessWindowHint`. Leave the title unset; Qt uses `QApplication.applicationName`.

**Click-outside dismiss polls `GetForegroundWindow`, not `focusOutEvent`.** The dashboard uses `BypassWindowManagerHint` and never has focus, so `focusOutEvent` never fires. The polling loop compares the new foreground's PID against ours to ignore inside-dashboard clicks. See `dashboard._check_outside_click`.

**Apply `WS_EX_TOOLWINDOW` after `setVisible(True)`.** Without it, the dashboard gets a taskbar slot the moment a child widget activates. See `dashboard._show_no_activate`.

**Read JSON state with `encoding="utf-8-sig"`.** PowerShell writes UTF-8 with a BOM by default; standard `json.loads` rejects it. All loaders in `settings.py` and `persistence.py` tolerate the BOM.

## OpenRouter API gotchas

- User keys (`sk-or-v1-...`) cannot call `/api/v1/activity` or `/api/v1/keys`. Those need a management key. The `management_api_key` field in `settings.json` is reserved for this.
- `/api/v1/key` returns identical values for `usage`, `usage_daily`, `usage_weekly`, `usage_monthly` when all activity falls within today. Not a bug.
- `pricing.prompt` and `pricing.completion` in `/api/v1/models` are STRINGS. Convert with `float()`.

## Safe restart during development

```powershell
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
  Where-Object { $_.CommandLine -like '*<your-clone-dir>*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Do not `Stop-Process python*`. It kills unrelated Python processes.
