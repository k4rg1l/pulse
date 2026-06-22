# AGENTS.md

For anyone (or anything) editing this repo. Read this before you start. The invariants below exist because we hit each one the hard way; honoring them saves you from re-discovering bugs that took real time to find.

## Run

```powershell
pip install -r requirements.txt
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
python main.py
```

## Validate before merging

**Full testing guide: [docs/TESTING.md](docs/TESTING.md).** It is the source of truth for *how* to test — the `pytest` layer for pure logic, the Windows-MCP recipes for driving the live UI, every automation gotcha, and a per-feature recipe table. Read it before validating; expand it whenever you ship a feature. The 20-point checklist below is the *what*; TESTING.md is the *how*.

Run the automated tests first: `pip install -r requirements-dev.txt && python -m pytest -q` (all green, sub-second).

Then open the dashboard and check every item. Skip none. Take screenshots if a UI change is involved.

1. App starts with no exceptions (`python main.py`, watch stderr).
2. Tray icon visible, tooltip shows the balance.
3. Left-click the tray opens the dashboard.
4. Right-click the tray shows the FULL menu, not truncated under the taskbar.
5. Clicking INSIDE the dashboard does NOT close it.
6. Clicking OUTSIDE the dashboard DOES close it.
7. The refresh button refetches.
8. No phantom "Python" entry appears in the taskbar when the dashboard opens.
9. "Open Settings File..." opens settings.json in the default editor.
10. The Exit menu actually quits the process.
11. State persists across a restart (settings and snapshot history).

If your change touches Pinned Models, also verify:

12. Section header chevron (▾/▸) is visible and clicking it toggles the section, even when the search bar has focus.
13. Search bar opens a dropdown that OVERLAYS the cards (cards stay visible underneath).
14. Pinned models appear at the top of the dropdown with a filled star (★); unpinned below with hollow star (☆).
15. Clicking a star toggles pin status, persists to settings.json, and the cards update without closing the dropdown.
16. Scrolling the dashboard closes the dropdown and any open info popup.
17. The (i) icon on each pinned card opens a popup floating OUTSIDE the dashboard. Click again or click outside to dismiss.
18. Switching from a many-provider model to a few-provider one SHRINKS the popup.
19. Long model names elide cleanly ("Nano Banana 2 (Gemini 3.1 Fla…") without overlapping the ★ chip.
20. Dashboard opens at the top every time, regardless of where you last scrolled.

## Invariants (Qt + Windows)

These exist because we hit them the hard way. Breaking any one corrupts behavior in non-obvious ways.

**`OpenRouterPulse` must inherit `QObject`.** Cross-thread signals from the worker only marshal into the main event loop when the receiver is a `QObject`. A plain Python class corrupts the GUI heap. `super().__init__()` must run after `QApplication` exists.

**Automatic cyclic GC is disabled; collection runs only on the main thread.** `OpenRouterPulse.__init__` calls `gc.disable()` and runs `gc.collect()` on a main-thread `QTimer` (`_collect_garbage`). DO NOT re-enable automatic GC. With worker threads doing heavy parsing (JSON catalog, Claude JSONL), the cyclic collector would otherwise fire *on a worker thread* and, because Qt's C++ paint releases the GIL, race a live `paintEvent` on the main thread — deterministically reproduced as an **access violation** (`get_*_info` → GC concurrent with `BurnRateBar`/`TimelineChart` paint). Refcount cleanup is unaffected; only cycle collection moves to the main thread, where it can't race a paint. (The benign `0x8001010d` COM dumps faulthandler sometimes logs at `app.exec()` are *not* this crash — the app survives those.)

**Never `setWindowTitle()` on the dashboard.** Setting a title produces a brief ghost window with a native frame before Qt applies `FramelessWindowHint`. Leave the title unset; Qt uses `QApplication.applicationName`.

**Click-outside dismiss polls `GetForegroundWindow`, not `focusOutEvent`.** The dashboard uses `BypassWindowManagerHint` and never has focus, so `focusOutEvent` never fires. The polling loop compares the new foreground's PID against ours to ignore inside-dashboard clicks. See `dashboard._check_outside_click`.

**Apply `WS_EX_TOOLWINDOW` after `setVisible(True)`.** Without it, the dashboard gets a taskbar slot the moment a child widget activates. See `dashboard._show_no_activate`.

**Read JSON state with `encoding="utf-8-sig"`.** PowerShell writes UTF-8 with a BOM by default; standard `json.loads` rejects it. All loaders in `settings.py` and `persistence.py` tolerate the BOM.

**Top-level `Qt.WindowType.Tool` windows don't render reliably as children of a `BypassWindowManagerHint` dashboard.** For overlay widgets like the picker dropdown, use a normal `QWidget` reparented to the dashboard via `setParent` and positioned with `.move()` + `.raise_()`. The dashboard's BypassWindowManager attribute confuses Qt's window-manager interactions for descendant top-level tool windows. See `ModelPicker.attach_overlay_to`. For floating popups that need to extend OUTSIDE the dashboard (like the provider info popup), top-level `Tool` windows DO work because they're not children of the dashboard.

**`QWidget.adjustSize()` GROWS but does NOT shrink.** After `setText` with smaller content, the widget keeps its previously-larger geometry. To force shrink, call `widget.resize(widget.sizeHint())` AFTER `adjustSize()`. Applies to the info popup whenever you swap content. See `ProviderPopup.show_beside`.

**Event filter geometry checks must be in matching coordinate spaces.** `widget.geometry()` returns coords in the PARENT's space, not global. To check whether a global mouse position is inside a widget, build the global rect: `QRect(widget.mapToGlobal(QPoint(0,0)), widget.size())`. Comparing global pos to parent-local rect silently never matches. See `ModelPicker.eventFilter`.

**Chevron QLabel inside SectionHeader needs `WA_TransparentForMouseEvents`.** Otherwise clicks on the small chevron glyph get consumed by the QLabel and never bubble to the parent SectionHeader's `mousePressEvent`. Result: clicks on the chevron specifically don't trigger collapse. See `SectionHeader.__init__`.

**`QScrollArea` preserves scroll position across hide/show.** When you want every dashboard open to start at the top (good UX), reset the vertical scrollbar manually: `self._scroll_area.verticalScrollBar().setValue(0)`. See `Dashboard.show_near_tray`.

**`html` rich text in QLabel: `white-space: nowrap` on EVERY cell, not just the first.** Otherwise short values like "170 t/s" can wrap mid-cell to "170 t/" + "s" when the column is just barely too narrow. Setting nowrap on the provider column alone doesn't help the metric columns.

**PyInstaller windowed builds have `sys.stderr = None`.** Any call to `faulthandler.enable()`, `print()`, or any other stderr write crashes the frozen .exe. At module load, check `sys.stderr is None` and redirect both stdout and stderr to a log file (we use `%APPDATA%/Pulse/pulse.log`). See `main._redirect_streams_if_frozen`. This is the most common reason a `.exe` that runs fine in dev crashes on first launch.

**PyInstaller .exe tray icons land in the hidden-icons overflow on first run.** Windows treats each unique exe path as a new application for tray-icon placement. The user has to drag the icon to the visible tray once. Don't try to "fix" this with shenanigans; just document it.

## OpenRouter API gotchas

- User keys (`sk-or-v1-...`) cannot call `/api/v1/activity` or `/api/v1/keys`. Those need a management key. The `management_api_key` field in `settings.json` is reserved for this.
- Management keys are organization-scoped only. There's no user-scoped management key. To make `/activity` return populated rows, the user must route their OpenRouter inference through an org-scoped API key, not just paste a management key.
- `/api/v1/key` returns identical values for `usage`, `usage_daily`, `usage_weekly`, `usage_monthly` when all activity falls within today. Not a bug, just the API's flat reporting.
- `pricing.prompt` and `pricing.completion` in `/api/v1/models` are STRINGS. Convert with `float()`.
- `/api/v1/models/{slug}/endpoints` returns `latency_last_30m` as a dict `{p50, p75, p90, p99}`, not a single number. Extract `p50` for the headline metric. Same shape for `throughput_last_30m`. See `EndpointInfo` + `_percentile` in `api_client.py`.
- The dashboard URL field on each provider in `/api/v1/providers` is mostly unused by us today, but the data is there if a future feature wants per-provider status-page deep links.

## Sources (the agnostic architecture)

Pulse is **not** OpenRouter — it's a monitor that shows many sources side by side. The dashboard is a neutral host: it renders an ordered list of **source section-groups** (`settings.source_order`); no provider is privileged. OpenRouter, Claude, GPU, and System are all peers.

A **Source** (`sources/base.py`) is a self-contained unit:
- `source_id` / `display_name` / `poll_interval` (seconds).
- `is_available() -> bool` — MAIN thread, cheap. Should this source show at all? (e.g. creds present, GPU detected, not hidden by a `show_*` setting.)
- `poll() -> data` — **WORKER thread**. Does all I/O, returns a plain data object, **must not touch Qt and must not raise** (return data carrying an error instead).
- `build_card(parent) -> QWidget` — MAIN thread. Returns a widget with `render(data)`.

The controller (`main.py`) polls each available source on a dedicated `source_thread` (a `SourceWorker` + `SourceTrigger`, mirroring the OpenRouter `APIWorker`/`FetchTrigger`), then marshals the result to `card.render(data)` on the main thread via the `polled` signal. OpenRouter keeps its bespoke multi-cadence worker but mounts as a peer group via `Dashboard.mount_source`.

**To add a source** (e.g. GitHub, FX): create `sources/<name>/` with a pure parser (unit-tested against a captured sample — never the live endpoint), a `<Name>Card(QWidget)` whose geometry is **font-metric-driven** (see `sources/gpu/card.py` — share one `_build_ops()` between `paintEvent` and height so nothing clips), and a `<Name>Source(Source)`. Add it to `OpenRouterPulse._SOURCE_CLASSES`, add a `show_<name>` setting + its id to `source_order`'s default. That's it — no dashboard changes.

**Source threading rules:** `poll()` is the only place a source does I/O, and it's Qt-free. The card lives on the main thread. Heavy parsing in `poll()` is fine (it's off the main thread) — but remember automatic GC is disabled (see the invariant above), so cyclic collection won't race a paint.

## Safe restart during development

```powershell
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
  Where-Object { $_.CommandLine -like '*OpenRouterPulse*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Do not `Stop-Process python*`. It kills unrelated Python processes (Claude Code's MCP backends, other Python tools you have running).

## Common imports easy to forget

When adding new code to `widgets.py`, the most-forgotten imports are:

```python
from PySide6.QtCore import QPoint, QRect, QEvent
from PySide6.QtGui import QFontMetrics, QCursor
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect
```

`QPoint`/`QRect`/`QFontMetrics` come up the moment you touch widget geometry. `QApplication` is needed any time you install an event filter. Errors from missing imports are obvious (`NameError`) but the windowed PyInstaller build hides them — if you're debugging a feature that "doesn't seem to do anything," check `%APPDATA%/Pulse/pulse.log` for the traceback.

## Handoff: what the next agent should know

**Current state:**
- Tag `v0.5.0` is the current release on `main` (Claude usage source, first `pytest` suite, the GC-crash fix). Pulse.exe attached to the GitHub release.
- The `feat/agnostic-sources` branch (PR #1) makes Pulse fully **source-agnostic** (neutral dashboard host; OpenRouter is a peer) and adds **GPU** + **System** sources and a **global hotkey**. See the Sources section above; review/merge that PR for the v0.6 feature set.
- Code is stable. 11-point validation passes, plus the 9 additional Pinned-Models checks above.
- The dashboard's three sections (Credit Balance, Usage, Burn Rate) and the Pinned Models section are the entire UI. Quick Links row at the bottom links to OpenRouter pages.
- User's `%APPDATA%/Pulse/settings.json` has both an org-scoped `api_key` and an org-scoped `management_api_key`. The management key is there but unused by the code yet.

**Recent intentional decisions:**
- Dropdown is an overlay child of the dashboard, not a top-level window. Tried both; child works more reliably.
- Provider info popup IS a top-level Tool window (because it needs to extend outside the dashboard). That works because it's not parented to the dashboard.
- Long model names elide rather than wrap. The user explicitly preferred this over alternatives.
- Empty-state for "no pinned models" is one line. The picker right above already prompts the user on how to add some.
- Validation discipline matters: the user has been burned by shipping without it. After any UI change, run the 20-point list above and present screenshots before committing.

**What's likely to come next:**
The user has expressed interest in (in rough order):
1. Notifications (top-up triggered, balance-out, provider-outage on pinned, daily summary)
2. Cost calculator widget
3. Per-model spend via `/activity` once the management-key data accumulates
4. Settings GUI dialog

See `ROADMAP.md` for the longer view.

**Don't:**
- Add features the user hasn't asked for.
- Touch `git` without explicit approval. Validate first, ASK, then commit.
- Bring back QToolTip on pinned cards. The click-to-toggle popup is intentional.
- Hide the cards when the picker opens. They overlay; cards stay visible.
- Add a source for a provider with no real programmatic usage API, or one that doesn't degrade gracefully when its data/credentials are absent. Multi-source is now built (Claude/GPU/System) — keep every new source honest, optional, and self-hiding (`is_available()` + a `show_*` setting).
- Refresh/rotate the Claude OAuth token. Pulse reads it strictly read-only; rotating it could log the user out of Claude Code. See `sources/claude/credentials.py`.

**Useful coordinates for visual testing (single-monitor 3440-wide setup):**
- Tray icon: ~(3229, 1416)
- Dashboard when open: (3020, 712, 420, 680)
- Search bar position depends on scroll; query via Windows-MCP Snapshot to find it fresh each time.

**Safe wheel-scroll approach in test scripts:**
PowerShell `mouse_event` with `MOUSEEVENTF_WHEEL` (0x0800) and `dwData = (uint32::MaxValue - 120 + 1)` for one notch down. Move cursor over the dashboard FIRST so the event lands there. If the cursor is on the dropdown when picker is open, the wheel scrolls the dropdown's internal scroll area instead.
