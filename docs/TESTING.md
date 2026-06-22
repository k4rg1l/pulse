# TESTING.md — How to test Pulse, correctly, every time

**This is the single source of truth for testing Pulse.** If you are an agent (or
human) working on this repo: read this before you validate anything, and follow it
*whole-heartedly*. It exists because testing a frameless, never-focused, custom-painted
Windows tray app is full of non-obvious traps — every gotcha below was hit for real and
cost time. Honor them and you skip the pain.

There are **two layers**, and a change is not "done" until the relevant layer(s) pass:

1. **Automated unit tests** (`pytest`) — for *pure logic* (math, parsing, persistence,
   settings). Fast, deterministic, run on every change. No GUI, no network.
2. **Manual E2E validation** (Windows-MCP, driving the live app) — for *anything visual
   or interactive*. The dashboard is hand-painted and frameless; only a real run proves it.

> **The prime directive (from AGENTS.md): validation is the most important part.**
> After any UI-affecting change, run the relevant manual recipes below *and the 20-point
> checklist in AGENTS.md*, capture screenshots, and **stop and ask the user to verify
> before committing or releasing.**

---

## Part 1 — Automated tests (pytest)

### Run them

```powershell
pip install -r requirements-dev.txt   # one-time: pulls pytest
python -m pytest -q                    # from the repo root
```

Expected: all green, sub-second. The suite lives in `tests/` with `conftest.py` at the repo
root (the root conftest makes the top-level modules importable from `tests/`).

### The safety rule that must never be broken

`persistence.state_dir()` resolves to the user's **real** `%APPDATA%/Pulse`, holding their
live `settings.json` and `state.json` (history, API keys). A test that calls
`Settings.load/save` or `History.load/save` against the real dir could clobber user data.

**`conftest.py` has an autouse fixture (`isolate_appdata`) that redirects `APPDATA` to a
per-test temp dir for *every* test.** Never remove or weaken it. Any new file-touching test
inherits the isolation automatically — do not write tests that bypass it.

### What's covered today

| File | Covers |
|---|---|
| `tests/test_persistence.py` | `Snapshot.balance` clamping, `History.add` dedup, `burn_in_window` (incl. ignoring top-up credit jumps), `burn_rate_per_hour` extrapolation + short-span guard, `topup_events` detection + sub-cent noise rejection, save/load round-trip, 90-day prune |
| `tests/test_settings.py` | defaults on first run, **unknown-key dropping** (forward-compat), missing-key fallback, **UTF-8 BOM tolerance**, corrupt-JSON fallback, `autotopup_enabled`, round-trip |
| `tests/test_api_client.py` | `KeyInfo` derived props (`remaining`/`credit_percent`/`days_remaining`), `$/token → $/Mtok` conversions, `EndpointInfo.uptime` 30m→1d→5m fallback, `ModelEndpoints.best_provider` (lowest latency among ≥99% uptime, cheaper-prompt tiebreak) |

### The rule for new code

- **Pure logic** (no Qt, no live network) → **add a unit test**. This is mandatory for
  anything doing math, parsing an API/file response, or transforming data. New data sources
  (e.g. the Claude usage parser, the JSONL token aggregator) are *exactly* this — they get
  tested against captured sample payloads, never the live endpoint.
- **Qt widgets / window mechanics / live HTTP** → can't be meaningfully unit-tested; covered
  by the manual recipes in Part 2.
- Keep tests deterministic: build timestamps relative to `time.time()`; never rely on wall
  clock, network, `Date.now()`, or sleep.

### Concurrency / GC crash class (validate this when adding threads or heavy parsing)

A native **access violation** can occur if the cyclic garbage collector runs on a *worker*
thread (e.g. during JSON/JSONL parsing) while the main thread is mid-`paintEvent` — Qt's C++
paint releases the GIL, the worker runs, its allocations trip cyclic GC, and the collection
races the live paint. We hit this when the Claude source's second worker thread raised GC
frequency. **Mitigation in code:** `gc.disable()` + main-thread `gc.collect()` timer (see the
AGENTS.md invariant — never re-enable auto GC). **How to stress-test it** (it's a race, so
unit tests can't catch it): drive the real app headless and hammer the worker paths while the
main thread paints:

```python
# QT_QPA_PLATFORM=offscreen python this; without the fix it segfaults (exit 139),
# with the fix it survives (exit 0). Used to reproduce + verify the GC crash.
import faulthandler; faulthandler.enable()
from PySide6.QtCore import QTimer
import main as M
pulse = M.OpenRouterPulse(); pulse.dashboard.show()
def hammer():
    pulse._refresh_all()
    if getattr(pulse, "sources", None):
        pulse.source_trigger.poll.emit(pulse.sources[0].source_id)
QTimer(pulse).timeout.connect(hammer)  # plus a tight timeline.repaint() timer; run ~40s
```
Run this whenever you add a worker thread, a new source, or heavy parsing.

---

## Part 2 — Manual E2E validation (Windows-MCP)

### Environment (this machine; re-derive per setup)

- Multi-monitor, **primary monitor is 3440×1440**, taskbar at the bottom.
- **Pulse tray icon: `(3229, 1416)`** on the primary monitor (visible tray, not overflow).
- **Dashboard** anchors its bottom-right to the tray: width is `420` (`config.DASHBOARD_WIDTH`),
  it opens above the tray. At default height the header sits near the top with
  **✕ close ≈ `(3395, 746)`** and **↻ refresh ≈ `(3359, 746)`**. Lower sections
  (Burn Rate, Pinned Models) are **below the fold — scroll to reach them.**
- These coordinates are environment- and scroll-specific. Always confirm with a screenshot.

### The golden rules (every one learned from a real failure)

1. **Open/close the dashboard with `Click` by *label*, not coordinates.** Snapshot first to
   get the current label of the `Pulse` taskbar button, then `Click(label=N)`. Raw
   `mouse_event` at `(3229,1416)` is **unreliable** for tray activation (observed: it did not
   open). Use `mouse_event` only for *in-window* actions after the dashboard is open.
2. **Standard child controls (✕, ↻) are label-clickable** and report real coordinates.
3. **The search field and ALL custom-painted widgets are invisible to the UI tree.** The
   gauge, section headers, chevron, pinned-model cards, the ⓘ info icon, picker rows, and the
   timeline are drawn in `paintEvent` — they do **not** appear in `Snapshot`. The search
   `QLineEdit` and quick-link buttons *appear* but report coords `(0,0)`. **Drive all of these
   by coordinate `mouse_event`, never by label.**
4. **NEVER `Type`-by-label into the search field.** It types at `(0,0)`, which lands in
   whatever window is focused (observed: it typed into the Claude app and **dismissed the
   dashboard**). Correct way: `mouse_event`-click the field to focus it, then send text with
   `SendKeys`.
5. **Anything that lands outside the dashboard dismisses it.** Click-outside dismiss polls the
   foreground window (the dashboard is never focused). This is **validation item #6 — a
   feature.** During testing it means: keep clicks inside the dashboard rect, or expect it to
   close. Clicking *inside* (e.g. the search field) keeps it open because the foreground PID
   then matches Pulse's.
6. **Windows-MCP param quirks (this client):** list/float params get stringified and rejected.
   - `Click`/`Move`: use `label`, not `loc=[x,y]` (`loc` fails validation here).
   - `Snapshot`: omit `display=[0]` (list fails); take the full snapshot.
   - `Wait`: integer seconds only (`duration=1`, not `0.6`).
   - For precise coordinates, use the **PowerShell `mouse_event` helper** below.
7. **Screenshots scale.** With both monitors captured the image is ~5360 wide and downscaled;
   with only the primary it's 3440×1440 (≈1:1 when displayed at 3440). To convert a displayed
   pixel to a screen coord, multiply by `original_width / displayed_width`. The tray and
   dashboard are always on the **primary** monitor — prefer reading coords from a
   primary-only screenshot.
8. **Launching via a new exe path sends the tray icon to the hidden-icons overflow.** Windows
   keys tray-icon placement by exe path, so launching via `pythonw.exe` (or a fresh
   `python.exe` path, or the `.exe` build's first run — the AGENTS.md quirk) drops the icon
   into the overflow rather than the visible tray. To reach it: `Click(label="Show Hidden
   Icons")`, `Snapshot` the "System tray overflow window", then `Click(label=<Pulse>)`. When
   the icon is in the overflow, `show_near_tray` can't read its rect, so the dashboard
   falls back to bottom-right placement (also fine to test). Keep using the same launch path
   across a test session to keep the icon put.

### The reusable mouse helper (PowerShell)

Use this for scrolling and for clicking painted regions. `Click` focuses widgets safely when
the target is inside the dashboard; `Scroll` needs the cursor over the dashboard body.

```powershell
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public class PulseMouse {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, int d, int e);
  public static void Click(int x,int y){ SetCursorPos(x,y); System.Threading.Thread.Sleep(150);
    mouse_event(0x0002,0,0,0,0); System.Threading.Thread.Sleep(50); mouse_event(0x0004,0,0,0,0); }
  public static void Scroll(int x,int y,int notches){ SetCursorPos(x,y); System.Threading.Thread.Sleep(150);
    for(int i=0;i<Math.Abs(notches);i++){ mouse_event(0x0800,0,0, notches<0?-120:120,0); System.Threading.Thread.Sleep(70);} }
}
"@
# Scroll the dashboard down 4 notches (cursor over the body):
[PulseMouse]::Scroll(3230,1050,-4)
# Click a painted control (e.g. the search field), then type into it:
[PulseMouse]::Click(3222,1232); Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait("claude")
```
`mouse_event` wheel deltas: `-120` = one notch down, `+120` = up (matches AGENTS.md).

### The core loop

1. `Snapshot` → find the `Pulse` taskbar button label → `Click(label=N)` to open.
2. `Screenshot` (annotation off) → confirm render; read the target control's coords (apply the
   downscale ratio if the image isn't 1:1).
3. If the target is below the fold: `[PulseMouse]::Scroll(<centerX>,<midY>,-N)` then re-screenshot.
4. Act: `[PulseMouse]::Click(x,y)` for painted controls; `SendKeys` for text; `Click(label)` for ✕/↻.
5. `Screenshot` → verify the expected state change.
6. Close via `Click(label)` on the tray (toggle) or ✕, or click outside.

### Safe restart during development (from AGENTS.md — do not deviate)

```powershell
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
  Where-Object { $_.CommandLine -like '*OpenRouterPulse*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
Never `Stop-Process python*` — it kills unrelated Python (MCP backends, other tools). Then
relaunch: `python main.py` (watch stderr for exceptions; in a frozen build, check
`%APPDATA%/Pulse/pulse.log`).

---

## Part 3 — Per-feature manual recipes

Each recipe = precondition → steps → expected. `[V]` = verified end-to-end against the live
app on 2026-06-21. `[T]` = uses a technique verified the same session but this specific control
wasn't individually clicked — verify on first use. These complement (don't replace) the
20-point checklist in AGENTS.md; the item numbers below reference it.

| # | Feature | Steps | Expected | Covers |
|---|---|---|---|---|
| 1 | **Tray tooltip** `[V]` | Hover the tray icon (or read its Snapshot name) | Shows balance / today / recent burn / auto-top-up lines | #2 |
| 2 | **Open dashboard** `[V]` | Snapshot → `Click(label=<Pulse tray>)` | Frameless dark panel anchored above the tray; renders gauge, balance, timeline, KPIs | #3 |
| 3 | **Opens at top** `[V]` | Scroll down, close, reopen | Reopens scrolled to the top (Credit Balance), not where you left it | #20 |
| 4 | **Refresh** `[T]` | `Click(label=↻)` | Data refetches (watch values/burn update) | #7 |
| 5 | **Scroll** `[V]` | `[PulseMouse]::Scroll(3230,1050,-4)` | Reveals Burn Rate + Pinned Models below the fold | — |
| 6 | **Search / filter** `[V]` | `[PulseMouse]::Click(<search field>)` → `SendKeys "claude"` | Dropdown opens, **overlays** the cards (cards stay visible), filters to matches | #13 |
| 7 | **Pinned-at-top + stars** `[V]` | While filtered | Pinned models on top with filled ★; unpinned below with hollow ☆ | #14 |
| 8 | **Pin** `[T]` | `[PulseMouse]::Click(<a hollow-☆ row>)` | Star fills; a new card appears; `tracked_models` in settings.json updates; dropdown stays open | #15 |
| 9 | **Unpin** `[T]` | `[PulseMouse]::Click(<a filled-★ row>)` | Star hollows; card removed; settings.json updates; dropdown stays open | #15 |
| 10 | **Chevron collapse/expand** `[T]` | `[PulseMouse]::Click(<▾ left of "PINNED MODELS">)` | Toggles the whole section (cards + search + column header) hide/show; chevron ▾↔▸; works even with search focused | #12 |
| 11 | **Info popup open** `[T]` | `[PulseMouse]::Click(<ⓘ icon, top-right of a pinned card>)` | A bordered popup floats **outside** the dashboard (left side) with the full provider table | #17 |
| 12 | **Info popup dismiss** `[T]` | Click the ⓘ again, or click outside the popup | Popup hides | #17 |
| 13 | **Popup shrinks** `[T]` | Open popup for a many-provider model, then a few-provider one | Popup shrinks to fit (doesn't keep the larger size) | #18 |
| 14 | **Long names elide** `[V]` | Pin a long-named model | Name elides with "…", never overlaps the ★ provider chip | #19 |
| 15 | **Scroll dismisses overlays** `[T]` | Open the picker or info popup, then scroll | Picker/popup close (don't float detached) | #16 |
| 16 | **Click-outside dismiss** `[V]` | Click anywhere outside the dashboard | Dashboard hides | #6 |
| 17 | **Click inside stays open** `[V]` | Click the search field (inside) | Dashboard stays open | #5 |
| 18 | **No taskbar entry** `[T]` | Open dashboard, check the taskbar | No phantom "Python"/dashboard button appears | #8 |
| 19 | **Right-click menu** `[T]` | `Click(label=<Pulse tray>, button=right)` | Full menu, not truncated under the taskbar; Open Dashboard / Refresh / Quick Links / Open Settings File / Start with Windows / Exit | #4, #9, #10 |
| 20 | **Close** `[V]` | `Click(label=✕)` | Dashboard hides; any open popup/picker closes too | — |
| 21 | **Persistence** `[T]` | Pin a model, restart app | Pinned set + snapshot history survive the restart | #11 |
| 22 | **Claude source appears** `[V]` | `~/.claude/.credentials.json` present; open dashboard, scroll past Pinned Models | A "Claude" section shows utilization bars (5h / 7d / Sonnet) with % + reset countdowns, severity colour, and a 7-day token footer (tokens · cached % · msgs · model split) | source |
| 23 | **Claude hidden via toggle** `[T]` | Set `show_claude: false` in settings.json, restart | The Claude section does not render; OpenRouter sections unaffected | source |
| 24 | **Claude degrades gracefully** `[T]` | Token expired, or no network | Usage area shows "Open Claude Code to refresh usage" / "unavailable"; the local 7-day token footer still shows | source |

For a new **data source**, also unit-test its pure parser against a captured sample payload
(see `tests/test_claude_usage.py` / `tests/test_claude_jsonl.py` as the template) — never hit
the live endpoint from a test. When you add any UI feature, **add a row (or a new recipe
block) here** and mark it `[V]` once you've driven it live.

---

## Part 4 — Extending this doc (do this for every feature you ship)

1. **Unit tests** for the feature's pure logic in `tests/` (see Part 1's rule). New data
   sources test their parser against a captured sample payload — never the live endpoint.
2. **A manual recipe** in Part 3 (precondition → steps → expected → checklist item), driven
   live and marked `[V]`.
3. **Any new gotcha** appended to Part 2's golden rules, so the next agent doesn't rediscover it.
4. If the feature changes window/focus/popup behavior, **also update AGENTS.md** (its invariants
   and 20-point checklist are the canonical window-mechanics contract; this doc is the
   how-to-test companion).

The goal: any agent can open this file and validate any feature correctly, with zero guesswork.
