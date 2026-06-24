"""
OpenRouter Pulse - Main Entry Point
System tray application for monitoring your OpenRouter subscription.
"""
import sys
import os
import ctypes
import gc
import time
import logging

# Configure structured logging + crash capture FIRST — before Qt is imported,
# so the frozen-build stream redirect (sys.stderr is None in a windowed .exe)
# happens up front and everything after logs through it. See logging_setup.py.
from logging_setup import setup_logging
setup_logging()
log = logging.getLogger("pulse.main")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, QTimer, Qt, Slot, Signal, QObject

from config import (
    APP_NAME, APP_ORG, API_KEY, ENDPOINTS_REFRESH_INTERVAL,
    CREDIT_WARNING_THRESHOLD, CREDIT_CRITICAL_THRESHOLD,
)
from theme import STYLESHEET, accent_for
from api_client import APIWorker
from tray_icon import TrayIcon
from dashboard import Dashboard
from persistence import History, Snapshot
from settings import Settings
from sources.worker import SourceWorker, SourceTrigger
from sources.claude.source import ClaudeSource
from sources.gpu.source import GpuSource
from sources.system.source import SystemSource
from hotkey import HotkeyListener


# ---------------------------------------------------------------------------
#  Single-instance enforcement (Windows named mutex)
# ---------------------------------------------------------------------------
_MUTEX_NAME = "Global\\Pulse_SingleInstance_v1"
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_lock():
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    last_err = ctypes.windll.kernel32.GetLastError()
    if last_err == _ERROR_ALREADY_EXISTS:
        return None
    return handle


class FetchTrigger(QObject):
    """Signals to trigger API fetches on the worker thread."""
    fetch_key = Signal()
    fetch_endpoints = Signal(str)   # model_id
    fetch_models = Signal()         # full catalog for the picker
    fetch_benchmarks = Signal()     # Arena standings (slow cadence)
    fetch_provider_trust = Signal() # provider privacy/trust posture (slow, no-auth)


class OpenRouterPulse(QObject):
    """Main application controller."""

    def __init__(self):
        self.app = QApplication(sys.argv)
        super().__init__()

        self.app.setApplicationName(APP_NAME)
        self.app.setOrganizationName(APP_ORG)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setStyleSheet(STYLESHEET)

        # -- Cyclic-GC safety (prevents a hard crash) --
        # PySide6 + worker threads can segfault if the cyclic garbage
        # collector runs ON a worker thread (deleting/finalizing a Qt
        # wrapper) WHILE the main thread is inside a paintEvent — Qt's C++
        # paint releases the GIL, a worker thread runs, its allocations
        # trip cyclic GC, and the collection races the live paint. Heavy
        # worker-thread allocation (JSON/JSONL parsing) makes GC fire on
        # those threads often enough to hit it reliably (reproduced as an
        # access violation: api-worker GC during get_*_info concurrent with
        # BurnRateBar/TimelineChart paint). Fix: turn off automatic cyclic
        # GC and run collection only on the MAIN thread via a timer, where
        # it cannot race a paint. Refcount cleanup is unaffected; only cycle
        # collection moves to the main thread.
        gc.disable()
        self._gc_timer = QTimer(self)
        self._gc_timer.timeout.connect(self._collect_garbage)
        self._gc_timer.start(5000)

        self.history = History.load()
        self.settings = Settings.load()

        import anim
        anim.set_enabled(getattr(self.settings, "enable_animations", True))

        # -- API worker thread --
        self.api_thread = QThread()
        self.api_worker = APIWorker()
        self.api_worker.moveToThread(self.api_thread)

        self.trigger = FetchTrigger()
        self.trigger.fetch_key.connect(self.api_worker.fetch_key_info)
        self.trigger.fetch_endpoints.connect(self.api_worker.fetch_endpoints)
        self.trigger.fetch_models.connect(self.api_worker.fetch_models)
        self.trigger.fetch_benchmarks.connect(self.api_worker.fetch_benchmarks)
        self.trigger.fetch_provider_trust.connect(self.api_worker.fetch_provider_trust)
        self.api_worker.key_info_ready.connect(self._on_key_info)
        self.api_worker.endpoints_ready.connect(self._on_endpoints)
        self.api_worker.models_ready.connect(self._on_models)
        self.api_worker.benchmarks_ready.connect(self._on_benchmarks)
        self.api_worker.provider_trust_ready.connect(self._on_provider_trust)
        self.api_worker.error.connect(self._on_error)

        self.api_thread.start()

        # -- Dashboard --
        self.dashboard = Dashboard(self.history, self.settings)
        self.dashboard.refresh_requested.connect(self._refresh_all)
        self.dashboard.refresh_endpoint_requested.connect(
            self.trigger.fetch_endpoints.emit
        )

        # -- Tray icon --
        self.tray = TrayIcon(self.settings)
        self.dashboard.set_tray_icon(self.tray)
        self.tray.toggle_dashboard.connect(self.dashboard.toggle)
        self.tray.refresh_requested.connect(self._refresh_all)
        self.tray.show()

        # -- Timers --
        self.key_timer = QTimer(self)
        self.key_timer.timeout.connect(self._fetch_key_info)
        self.key_timer.start(self.settings.key_refresh_seconds * 1000)

        # Endpoints refresh on its own cadence (slower than balance polling).
        self.endpoints_timer = QTimer(self)
        self.endpoints_timer.timeout.connect(self._fetch_all_endpoints)
        self.endpoints_timer.start(ENDPOINTS_REFRESH_INTERVAL)

        QTimer.singleShot(500, self._refresh_all)
        # Fetch the full model catalog once on startup so the picker is
        # ready as soon as the user clicks the search bar.
        QTimer.singleShot(800, lambda: self.trigger.fetch_models.emit())
        # Arena standings: fetch once shortly after launch, then refresh on a
        # slow cadence (benchmarks barely move day-to-day). Opt-out via setting.
        if getattr(self.settings, "show_arena", True):
            QTimer.singleShot(1200, lambda: self.trigger.fetch_benchmarks.emit())
            self.benchmarks_timer = QTimer(self)
            self.benchmarks_timer.timeout.connect(
                lambda: self.trigger.fetch_benchmarks.emit())
            self.benchmarks_timer.start(6 * 3600 * 1000)   # every 6 hours

        # Provider trust posture (The Ledger): no-auth, very slow-moving — fetch
        # once shortly after launch, then refresh every 12 hours. Opt-out setting.
        if getattr(self.settings, "show_trust_seals", True):
            QTimer.singleShot(1400, lambda: self.trigger.fetch_provider_trust.emit())
            self.trust_timer = QTimer(self)
            self.trust_timer.timeout.connect(
                lambda: self.trigger.fetch_provider_trust.emit())
            self.trust_timer.start(12 * 3600 * 1000)   # every 12 hours

        # -- Pluggable sources (Claude, …): peers to OpenRouter --
        self._setup_sources()

        # -- Global hotkey to summon the dashboard (Win32 RegisterHotKey) --
        self.hotkey = HotkeyListener(getattr(self.settings, "hotkey", "win+shift+o"))
        self.hotkey.summon.connect(self.dashboard.toggle)
        self.hotkey.start()

        # If no API key is set, surface it immediately
        if not API_KEY:
            self.dashboard.show_error(
                "No API key. Set OPENROUTER_API_KEY env var or edit settings.json "
                "(tray menu: Open Settings File...)."
            )

    @Slot()
    def _fetch_key_info(self):
        self.trigger.fetch_key.emit()

    @Slot()
    def _refresh_all(self):
        self.trigger.fetch_key.emit()
        self._fetch_all_endpoints()
        # Refresh the picker catalog too in case OpenRouter added new
        # models since launch.  It's a slow-changing list so we don't
        # do this every minute, only on manual refresh.
        self.trigger.fetch_models.emit()
        if getattr(self.settings, "show_arena", True):
            self.trigger.fetch_benchmarks.emit()
        if getattr(self.settings, "show_trust_seals", True):
            self.trigger.fetch_provider_trust.emit()
        # Peer sources (Claude/GPU/System) too — a manual refresh should
        # refetch everything, not just OpenRouter. force_refresh() lets a
        # source (e.g. Claude) break its usage-endpoint backoff and retry now.
        self._refresh_sources()

    def _refresh_sources(self):
        if getattr(self, "source_trigger", None) is None:
            return
        for src in getattr(self, "sources", None) or []:
            try:
                src.force_refresh()
            except Exception:
                pass
            self.source_trigger.poll.emit(src.source_id)

    def _fetch_all_endpoints(self):
        """Kick off an endpoints fetch for every pinned model."""
        for mid in self.dashboard.tracked_models():
            self.trigger.fetch_endpoints.emit(mid)

    @Slot(str, object)
    def _on_endpoints(self, model_id, model_endpoints):
        self.dashboard.update_endpoints(model_id, model_endpoints)

    @Slot(object)
    def _on_models(self, models):
        self.dashboard.update_model_catalog(models)

    @Slot(object)
    def _on_benchmarks(self, board):
        self.dashboard.update_benchmarks(board)

    @Slot(object)
    def _on_provider_trust(self, book):
        log.debug("provider trust: %s providers", len(book) if book else 0)
        self.dashboard.update_provider_trust(book)

    @Slot(object)
    def _on_key_info(self, key_info):
        if key_info.total_credits > 0 or key_info.usage > 0:
            snap = Snapshot(
                ts=time.time(),
                total_credits=key_info.total_credits,
                total_usage=key_info.total_usage,
                usage_daily=key_info.usage_daily,
                usage_monthly=key_info.usage_monthly,
            )
            if self.history.add(snap):
                QTimer.singleShot(0, self._save_history)

        log.debug("key_info ok: remaining=%s usage_daily=%s",
                  key_info.remaining, key_info.usage_daily)
        self.dashboard.clear_error()
        self.tray.clear_error()
        self.tray.update_credit_info(key_info, self.history)
        self.dashboard.update_key_info(key_info)
        self.dashboard.set_source_status(
            "openrouter", self._openrouter_severity(key_info))

    def _openrouter_severity(self, key_info):
        rem = key_info.remaining
        if rem is None:
            return "normal"
        if rem <= CREDIT_CRITICAL_THRESHOLD:
            return "critical"
        if rem <= CREDIT_WARNING_THRESHOLD:
            return "warning"
        return "normal"

    def _save_history(self):
        try:
            self.history.save()
        except Exception:
            log.exception("history save failed")

    @Slot(str)
    def _on_error(self, msg):
        log.error("OpenRouter API error: %s", msg)
        self.dashboard.show_error(msg)
        self.tray.set_error(msg)

    # ------------------------------------------------------------------
    #  Pluggable sources (Claude, …)
    # ------------------------------------------------------------------
    _SOURCE_CLASSES = (ClaudeSource, GpuSource, SystemSource)

    def _setup_sources(self):
        """Instantiate available sources, mount a card per source, and start
        a dedicated worker thread that polls them on their own intervals.
        Each step is guarded so a misbehaving source can't break startup."""
        self.sources = []
        self._source_by_id = {}
        self._source_timers = []
        self.source_thread = None

        for cls in self._SOURCE_CLASSES:
            try:
                src = cls(self.settings)
                if not src.is_available():
                    continue
                card = src.build_card()
            except Exception:
                log.exception("source %s setup failed", cls.__name__)
                continue
            self.dashboard.register_source_tab(
                src.source_id, src.display_name, accent_for(src.source_id), card)
            self.sources.append(src)
            self._source_by_id[src.source_id] = src

        # Pulse's own Settings tab (rail bottom gear), after all sources exist.
        self.dashboard.register_settings_tab()

        if not self.sources:
            return

        self.source_thread = QThread()
        self.source_worker = SourceWorker(self.sources)
        self.source_worker.moveToThread(self.source_thread)
        self.source_trigger = SourceTrigger()
        self.source_trigger.poll.connect(self.source_worker.poll)
        self.source_worker.polled.connect(self._on_source_polled)
        self.source_thread.start()

        for src in self.sources:
            interval = max(15, int(getattr(src, "poll_interval", 60)))
            timer = QTimer(self)
            timer.timeout.connect(
                lambda sid=src.source_id: self.source_trigger.poll.emit(sid)
            )
            timer.start(interval * 1000)
            self._source_timers.append(timer)
            # Kick an initial poll shortly after startup.
            QTimer.singleShot(
                1200, lambda sid=src.source_id: self.source_trigger.poll.emit(sid)
            )

    @Slot(str, object)
    def _on_source_polled(self, source_id, data):
        if data is None:
            return
        self.dashboard.update_source(source_id, data)
        src = self._source_by_id.get(source_id)
        if src is not None:
            try:
                self.dashboard.set_source_status(source_id, src.severity(data))
            except Exception:
                pass

    @Slot()
    def _collect_garbage(self):
        """Run cyclic GC on the MAIN thread only (automatic GC is disabled).
        Keeps cycle collection from racing a paintEvent on a worker thread."""
        gc.collect()

    def run(self):
        if API_KEY:
            self.tray.showMessage(
                APP_NAME,
                "Monitoring your OpenRouter subscription.\nClick the tray icon to open the dashboard.",
                self.tray.MessageIcon.Information,
                3000,
            )
        code = self.app.exec()
        if getattr(self, "hotkey", None) is not None:
            self.hotkey.stop()
        self.api_thread.quit()
        self.api_thread.wait(3000)
        if getattr(self, "source_thread", None) is not None:
            self.source_thread.quit()
            self.source_thread.wait(3000)
        try:
            self.history.save()
        except Exception:
            pass
        return code


def main():
    if _acquire_single_instance_lock() is None:
        app = QApplication(sys.argv)
        from PySide6.QtWidgets import QSystemTrayIcon, QStyle
        icon = app.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        tip = QSystemTrayIcon(icon)
        tip.show()
        tip.showMessage(
            APP_NAME,
            "OpenRouter Pulse is already running. Check your system tray.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )
        QTimer.singleShot(3500, app.quit)
        app.exec()
        return 0

    pulse = OpenRouterPulse()
    return pulse.run()


if __name__ == "__main__":
    sys.exit(main())
