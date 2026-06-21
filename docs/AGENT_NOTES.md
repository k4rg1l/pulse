# Notes for anyone working on Pulse

Stuff we learned the hard way. Keep it to things that don't change. If a fact is "true today but might be different tomorrow" it does not belong here. This file is for invariants.

## PySide6 on Windows: things that bit us

**Frameless + non-activating popups can't use focusOut.** The dashboard uses `Qt.WindowType.FramelessWindowHint | WindowStaysOnTopHint | BypassWindowManagerHint` plus `WA_ShowWithoutActivating`. The combination means the window never receives keyboard focus, so `focusOutEvent` never fires. To detect "user clicked elsewhere," poll `user32!GetForegroundWindow` from a `QTimer`. If the foreground HWND belongs to our process (check with `GetWindowThreadProcessId` and compare `os.getpid()`), treat it as "still us" and update the baseline. See `dashboard._check_outside_click`.

**Frameless windows can leak a taskbar entry when they get activated.** Even with `BypassWindowManagerHint`, clicking a child widget activates the window and Explorer slots it into the taskbar as "Python 3.14." Fix: after `setVisible(True)`, apply `WS_EX_TOOLWINDOW` and clear `WS_EX_APPWINDOW` via `SetWindowLongW(hwnd, GWL_EXSTYLE, ...)`. See `dashboard._show_no_activate`.

**Never call `setWindowTitle` on a frameless window.** On Windows it briefly creates a tool window with a native title bar before Qt applies the frameless hint, producing a ghost window at (0,0). Just leave the title unset; Qt falls back to `QApplication::applicationName`.

**Cross-thread signals require BOTH endpoints to be QObjects.** If the controller (the thing receiving worker signals) is a plain Python class, PySide6 can't dispatch the call into the main thread's event loop. The callback runs synchronously on the worker thread and corrupts the GUI heap. Make controllers inherit `QObject` and call `super().__init__()` AFTER `QApplication` exists. See the `OpenRouterPulse` class.

**`border-radius` on a parent does not clip children.** A QFrame with `border-radius: 12px` paints its own rounded background but child widgets still paint into their full rect, so anything reaching the corners pokes out. Clip in the child's `paintEvent` with a `QPainterPath.addRoundedRect`. The corner radius in the clip can exceed the child's height (only the topmost slice of the arc falls in the geometry). See `GradientStrip.paintEvent`.

**Size widgets with `QFontMetrics`, not magic numbers.** `14 + len(label) * 7` looked fine for short labels and clipped "Homepage" mid-letter. Use `QFontMetrics(font).horizontalAdvance(text)`. See `StatusBadge.__init__`.

**Show without focus theft = both Qt and Win32.** `WA_ShowWithoutActivating` alone isn't enough on Windows 11. Add a `SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)` call right after `setVisible(True)`. Otherwise the tray overflow panel closes the moment you click the icon.

**QSystemTrayIcon's setContextMenu auto-positions at the click.** A tall menu gets truncated under the taskbar. To anchor it the way the dashboard anchors (menu's bottom-right at the icon's top-left), don't call `setContextMenu`. Instead listen for `activated(QSystemTrayIcon.ActivationReason.Context)` and call `menu.exec(QPoint)` with your computed position, clamped to the screen that contains the icon.

**Notification toast requires a real QIcon.** Passing `QIcon()` (empty) prints `QSystemTrayIcon::setVisible: No Icon set` and the toast may not appear. Use `QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)` for placeholders.

## Windows specifics

**Single-instance lock: named mutex with `Global\` prefix.**
```python
handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\AppName_v1")
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    # another instance owns it
```
Hold the handle for the process lifetime. OS reclaims on exit.

**PowerShell 5.1 writes UTF-8 with a BOM.** When you do `Set-Content -Encoding utf8` or `ConvertTo-Json | Out-File`, the result has a `0xEF 0xBB 0xBF` prefix. Python's `json.loads` rejects it. Either write with `[System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding $false))` from PowerShell, or read with `encoding="utf-8-sig"` from Python. We do the latter everywhere that touches `settings.json` / `state.json`.

**`%APPDATA%` resolves to `C:\Users\<user>\AppData\Roaming`.** Use it for per-user, persistent state. NOT `%LOCALAPPDATA%` (machine-local, sometimes excluded from sync) and NOT `%PROGRAMDATA%` (requires admin to write).

**Startup registry entry lives at `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.** Value is the literal command line: `"<python.exe>" "<main.py>"`. Quoted because paths contain spaces. Deleting the value removes "Start with Windows."

## OpenRouter API constraints

**User keys can NOT call `/api/v1/activity` or `/api/v1/keys`.** Both return 401/403 with `"Only management keys can fetch X"`. A management key is a separate credential at openrouter.ai/settings/keys. We have a `management_api_key` field in settings for users who opt in.

**`/api/v1/key` returns identical values for `usage`, `usage_daily`, `usage_weekly`, `usage_monthly` when all activity happens within today.** Not a bug, just the API's flat reporting. Don't render four cards with the same number; show one Today value plus a derived projection.

**Pricing fields in `/api/v1/models` are STRINGS.** `pricing.prompt` and `pricing.completion` come back as `"0.00000013"`, not floats. Convert with `float()` and multiply by `1e6` for $/M tokens.

**Underused: `/api/v1/models/{author}/{slug}/endpoints`.** Per-provider uptime (5m, 30m, 1d), p50/p75/p90/p99 latency, throughput, quantization, and pricing. This is the source of truth for per-provider health.

**Status page (`status.openrouter.ai`) is OnlineOrNot, not Statuspage.** No JSON API. The HTML scrape for "all systems operational" is the most reliable signal we can get without scraping per-component DOM.

## GitHub workflow

**Email privacy blocks commits with your real email.** GitHub returns `GH007: Your push would publish a private email address`. Configure git to use the noreply form:
```bash
git config user.email "<id>+<username>@users.noreply.github.com"
```
Get your ID with `gh api user --jq '.id'`.

**`gh` on Windows installs `gh.cmd`; Git Bash doesn't honor PATHEXT.** Drop a tiny wrapper script at `~/.local/bin/gh` (no extension) that `exec`s `gh.cmd`. Then bare `gh` commands work from Bash.

**`gh repo rename <new>` updates the remote on GitHub** and adds a permanent redirect from the old URL. Your local remote still points at the old URL until you do `git remote set-url origin https://github.com/<owner>/<new>.git`.

## Repo layout assumptions baked in

- Local working tree: `C:\Users\Vatsal\OpenRouterPulse\`. We did NOT rename the local dir during the Pulse rebrand because it'd break the user's existing `Start with Windows` registry entry, which is a literal path to `main.py`. New clones can be anywhere.
- Per-user state: `%APPDATA%\Pulse\` (`settings.json`, `state.json`). `persistence.state_dir()` auto-migrates from the legacy `%APPDATA%\OpenRouterPulse\` directory if the new one is empty. Legacy dir is left in place as a safety backup.
- Registry startup name: `Pulse`. The tray icon's first run migrates a legacy `OpenRouterPulse` entry if present (see `tray_icon._migrate_legacy_startup_entry`).
- Mutex: `Global\Pulse_SingleInstance_v1`. Bump the `v1` suffix only if you intentionally want to break older versions out of the lock during a rollout.

## Safe restart command

Don't `Stop-Process python*` — it kills MCP backends and other Python tools too. Filter on the command line:
```powershell
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
  Where-Object { $_.CommandLine -like '*OpenRouterPulse*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Then relaunch:
```powershell
Start-Process -FilePath 'C:\Python314\python.exe' `
  -ArgumentList 'C:\Users\Vatsal\OpenRouterPulse\main.py' `
  -WorkingDirectory 'C:\Users\Vatsal\OpenRouterPulse' -WindowStyle Hidden
```

## Validation checklist after any UI change

Run this every time before declaring done. We added it after shipping a regression we should have caught.

1. App starts, no Python exceptions.
2. Tray icon visible with the right tooltip.
3. Left-click tray opens the dashboard.
4. Right-click tray shows the FULL menu (no taskbar truncation).
5. Click INSIDE the dashboard does NOT close it.
6. Click OUTSIDE the dashboard DOES close it.
7. Refresh button refetches.
8. Search filter works (if a search exists in the current layout).
9. NO phantom "Python 3.14" entry appears in the taskbar.
10. "Open Settings File..." opens the JSON in the default editor.
11. Exit menu actually quits the process (cleanup confirmed via `Get-CimInstance`).
