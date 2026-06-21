"""
OpenRouter Pulse - Main Entry Point
System tray application for monitoring your OpenRouter subscription.
"""
import sys
import ctypes
import faulthandler
import time

faulthandler.enable()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, QTimer, Qt, Slot, Signal, QObject

from config import APP_NAME, APP_ORG, API_KEY
from theme import STYLESHEET
from api_client import APIWorker
from tray_icon import TrayIcon
from dashboard import Dashboard
from persistence import History, Snapshot
from settings import Settings


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


class OpenRouterPulse(QObject):
    """Main application controller."""

    def __init__(self):
        self.app = QApplication(sys.argv)
        super().__init__()

        self.app.setApplicationName(APP_NAME)
        self.app.setOrganizationName(APP_ORG)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setStyleSheet(STYLESHEET)

        self.history = History.load()
        self.settings = Settings.load()

        # -- API worker thread --
        self.api_thread = QThread()
        self.api_worker = APIWorker()
        self.api_worker.moveToThread(self.api_thread)

        self.trigger = FetchTrigger()
        self.trigger.fetch_key.connect(self.api_worker.fetch_key_info)
        self.api_worker.key_info_ready.connect(self._on_key_info)
        self.api_worker.error.connect(self._on_error)

        self.api_thread.start()

        # -- Dashboard --
        self.dashboard = Dashboard(self.history, self.settings)
        self.dashboard.refresh_requested.connect(self._refresh_all)

        # -- Tray icon --
        self.tray = TrayIcon(self.settings)
        self.dashboard.set_tray_icon(self.tray)
        self.tray.toggle_dashboard.connect(self.dashboard.toggle)
        self.tray.refresh_requested.connect(self._refresh_all)
        self.tray.show()

        # -- Timer (single source of refresh) --
        self.key_timer = QTimer(self)
        self.key_timer.timeout.connect(self._fetch_key_info)
        self.key_timer.start(self.settings.key_refresh_seconds * 1000)

        QTimer.singleShot(500, self._refresh_all)

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

        self.dashboard.clear_error()
        self.tray.clear_error()
        self.tray.update_credit_info(key_info, self.history)
        self.dashboard.update_key_info(key_info)

    def _save_history(self):
        try:
            self.history.save()
        except Exception as e:
            print(f"[history] save failed: {e}")

    @Slot(str)
    def _on_error(self, msg):
        print(f"[Pulse] API Error: {msg}")
        self.dashboard.show_error(msg)
        self.tray.set_error(msg)

    def run(self):
        if API_KEY:
            self.tray.showMessage(
                APP_NAME,
                "Monitoring your OpenRouter subscription.\nClick the tray icon to open the dashboard.",
                self.tray.MessageIcon.Information,
                3000,
            )
        code = self.app.exec()
        self.api_thread.quit()
        self.api_thread.wait(3000)
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
