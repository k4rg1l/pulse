# Logging

Pulse logs everything through Python's stdlib `logging`, configured once in
[`logging_setup.py`](../logging_setup.py) (`setup_logging()`, called first thing
in `main`). No log cluster, no extra dependency — just structured files on disk.

## Where the logs are

```
%APPDATA%\Pulse\logs\
  pulse.jsonl        # structured app log — one JSON object per line (canonical, searchable)
  pulse.jsonl.1..7   # rotated backups (~2 MB each)
  pulse-crash.log    # faulthandler hard-crash dumps + raw stdout/stderr sink (frozen build)
```

Full path on this machine: `C:\Users\<you>\AppData\Roaming\Pulse\logs\`.

## Why JSON-lines (and not OpenSearch)

OpenSearch/Elastic is a JVM search **cluster** — wildly overkill for a
single-user desktop tray app. JSON-lines hits the same goals without the weight:

- **Searchable now** — every line is a JSON object, greppable with `rg`/`jq`/`Select-String`.
- **Indexable later** — the exact same stream ships into OpenSearch / Loki /
  Elastic / Datadog with **zero format change** if we ever want a search UI.

## Searching (give these to Claude — "check the logs")

```bash
# All errors/criticals
rg '"level":"ERROR"|"level":"CRITICAL"' "$APPDATA/Pulse/logs/pulse.jsonl"

# Just the OpenRouter API path
rg '"logger":"pulse.api"|"logger":"pulse.main"' "$APPDATA/Pulse/logs/pulse.jsonl"

# Pretty-print the last 30 lines
tail -30 "$APPDATA/Pulse/logs/pulse.jsonl" | jq .
```

PowerShell:
```powershell
Select-String -Path "$env:APPDATA\Pulse\logs\pulse.jsonl" -Pattern '"level":"ERROR"'
Get-Content "$env:APPDATA\Pulse\logs\pulse.jsonl" -Tail 30 | ForEach-Object { $_ | ConvertFrom-Json }
```

## Each line's shape

```json
{"ts":"2026-06-23T21:38:16.421+00:00","level":"DEBUG","logger":"pulse.main",
 "msg":"key_info ok: remaining=5.54 usage_daily=0.03","module":"main",
 "func":"_on_key_info","line":243,"thread":"MainThread"}
```

Pass structured context via `extra=` and it's merged into the line:
`log.info("fetched", extra={"remaining": 5.54})` → adds `"remaining":5.54`.
Errors logged with `log.exception(...)` include a full `"exc"` traceback.

## What's captured

- All `pulse.*` loggers (api, main, sources, hotkey, persistence, settings, dashboard, tray).
- Uncaught exceptions on the **main thread** (`sys.excepthook`) and **worker threads** (`threading.excepthook`).
- Qt's own messages (`qInstallMessageHandler` → `qt` logger).
- Python `warnings` (`captureWarnings`).
- Hard C-level crashes via `faulthandler` → `pulse-crash.log`.

Third-party noise (`urllib3`, `requests`, …) is pinned to WARNING so the file
stays focused on Pulse's own events.

## Conventions

- **Never `print()` in app code.** Use `log = logging.getLogger("pulse.<area>")`.
- **Never pass secrets via `extra=`.** Every `extra=` value is merged into the JSON line — pass only scalar, non-secret fields, never a `KeyInfo`/credentials/Authorization header.
- Inside `except`, prefer `log.exception("what failed")` — it auto-attaches the traceback.
- Routine/expected failures (network blips) → `log.warning`; bugs/unexpected → `log.exception`.
- Level is `DEBUG` to file by default; override with the `PULSE_LOG_LEVEL` env var.

## The benign `0x8001010d` dumps

`pulse-crash.log` may show `Windows fatal exception: code 0x8001010d`
(`RPC_E_CANTCALLOUT_ININPUTSYNCCALL`) from the hotkey thread at startup. It's a
COM re-entrancy first-chance exception faulthandler reports; **the app survives
it** (see AGENTS.md). It's isolated to the crash file so it never pollutes the
searchable `pulse.jsonl`.
