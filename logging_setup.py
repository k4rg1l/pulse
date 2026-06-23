"""Centralized logging for Pulse.

Production-minded but desktop-appropriate. We deliberately do NOT run a log
cluster (OpenSearch/Elastic) for a single-user tray app — that's a JVM search
*cluster*, wildly overkill here. Instead we write **structured JSON-lines** to
a rotating file under ``%APPDATA%/Pulse/logs/``:

  * greppable RIGHT NOW — ``rg '"level":"ERROR"' pulse.jsonl`` or PowerShell
    ``Select-String``; one JSON object per line, trivial to filter/query.
  * ship-able LATER with zero format change — the same JSON-lines stream drops
    straight into OpenSearch / Loki / Elastic / Datadog if you ever want
    indexing + a search UI.

Everything funnels through ``logging``: stdlib loggers, uncaught exceptions on
the main thread (``sys.excepthook``) and worker threads
(``threading.excepthook``), Python ``warnings``, and Qt's own messages
(``qInstallMessageHandler``). ``faulthandler`` still guards hard C-level
crashes, writing to its own file so the searchable log stays clean.

Files written (all under ``%APPDATA%/Pulse/logs/``):
  * ``pulse.jsonl``      — structured app log (canonical, searchable, rotating)
  * ``pulse-crash.log``  — faulthandler dumps + raw stdout/stderr sink for the
                            frozen windowed build (catches C-level / 3rd-party
                            output and the benign ``0x8001010d`` COM dumps)

The level is ``PULSE_LOG_LEVEL`` (env) or DEBUG for the file / INFO for console.
"""
from __future__ import annotations

import faulthandler
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

APP_DIRNAME = "Pulse"
_JSONL_NAME = "pulse.jsonl"
_CRASH_NAME = "pulse-crash.log"
_MAX_BYTES = 2_000_000      # ~2 MB per file
_BACKUPS = 7                # keep ~14 MB of history

_configured = False
_log_dir: Path | None = None
_crash_fp = None  # keep the faulthandler file handle alive for the process


def get_log_dir() -> Path:
    """``%APPDATA%/Pulse/logs`` (falls back to ~ if APPDATA is unset)."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(appdata) / APP_DIRNAME / "logs"


# Standard LogRecord attributes — anything NOT in here that a caller passes via
# ``extra=`` is treated as structured context and merged into the JSON line.
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonLineFormatter(logging.Formatter):
    """One compact JSON object per line. Stable keys first, then any
    structured ``extra=`` context, then exception/stack text.

    SECURITY: every JSON-serializable ``extra=`` value is merged into the line.
    NEVER pass secret-bearing objects (e.g. ``KeyInfo`` with its ``raw`` API
    response, credentials, Authorization headers) as ``extra=`` — pass only
    scalar, non-secret fields. There is no allowlist/scrubbing here by design."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "thread": record.threadName,
        }
        for key, val in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(val)  # only keep JSON-serializable extras
                    obj[key] = val
                except (TypeError, ValueError):
                    obj[key] = repr(val)
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            obj["stack"] = self.formatStack(record.stack_info)
        return json.dumps(obj, ensure_ascii=False)


def _ensure_streams(crash_path: Path) -> None:
    """PyInstaller windowed builds have ``sys.stdout/stderr is None`` — any
    ``print()``/``faulthandler``/3rd-party C write would crash. Point them at
    the crash log so stray output is still captured. See AGENTS.md."""
    global _crash_fp
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        _crash_fp = open(crash_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _crash_fp
        if sys.stderr is None:
            sys.stderr = _crash_fp
    except Exception:
        class _Null:
            def write(self, *a, **kw):
                pass

            def flush(self):
                pass
        if sys.stdout is None:
            sys.stdout = _Null()
        if sys.stderr is None:
            sys.stderr = _Null()


def _install_excepthooks() -> None:
    log = logging.getLogger("pulse.uncaught")

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Uncaught exception on main thread",
                     exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook

    def _thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        name = args.thread.name if args.thread else "?"
        log.critical("Uncaught exception on thread %r", name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = _thread_hook


def _install_qt_handler() -> None:
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return
    log = logging.getLogger("qt")
    level = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def _handler(mode, context, message):
        log.log(level.get(mode, logging.INFO), "%s", message)

    qInstallMessageHandler(_handler)


def setup_logging() -> Path:
    """Idempotent. Configure structured logging and crash capture, returning
    the log directory. Call this as early as possible in ``main`` — before Qt
    is imported, so frozen-build stream redirection happens first."""
    global _configured, _log_dir, _crash_fp
    if _configured:
        return _log_dir  # type: ignore[return-value]

    _log_dir = get_log_dir()
    try:
        _log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        _log_dir = Path(os.path.expanduser("~"))

    crash_path = _log_dir / _CRASH_NAME
    _ensure_streams(crash_path)

    level_name = os.environ.get("PULSE_LOG_LEVEL", "DEBUG").upper()
    file_level = getattr(logging, level_name, logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(min(file_level, logging.INFO))
    for h in list(root.handlers):  # avoid duplicate handlers on re-entry
        root.removeHandler(h)

    fh = logging.handlers.RotatingFileHandler(
        _log_dir / _JSONL_NAME, maxBytes=_MAX_BYTES, backupCount=_BACKUPS,
        encoding="utf-8", delay=True,
    )
    fh.setLevel(file_level)
    fh.setFormatter(JsonLineFormatter())
    root.addHandler(fh)

    # Human-readable console for dev runs (no console in the frozen build).
    if sys.stderr is not None and _crash_fp is None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        root.addHandler(ch)

    # Tame chatty third-party loggers — keep the file focused on Pulse events.
    # (Our api_client logs each fetch outcome itself, so we don't need urllib3's
    # per-connection DEBUG spam; warnings/retries still come through.)
    for noisy in ("urllib3", "requests", "PIL", "asyncio", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.captureWarnings(True)
    _install_excepthooks()
    _install_qt_handler()

    # faulthandler: hard C-level crashes -> dedicated file (kept open).
    try:
        if _crash_fp is None:
            _crash_fp = open(crash_path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_crash_fp)
    except Exception:
        try:
            faulthandler.enable()
        except Exception:
            pass

    _configured = True
    logging.getLogger("pulse").info(
        "logging started", extra={
            "version": _app_version(), "pid": os.getpid(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "log_dir": str(_log_dir),
        })
    return _log_dir


def _app_version() -> str:
    try:
        from config import APP_VERSION
        return APP_VERSION
    except Exception:
        return "?"
