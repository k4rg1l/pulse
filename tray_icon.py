"""
OpenRouter Pulse - System Tray Icon
Dynamic icon rendering, context menu, notifications.
"""
import os
import sys
import math
import subprocess
import webbrowser
import winreg
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QPen, QBrush,
    QConicalGradient, QRadialGradient, QImage, QAction, QCursor,
)
from PySide6.QtCore import Qt, Signal, QObject, QPoint, QPointF, QRectF

from theme import Colors, Fonts
from config import (
    APP_NAME, OPENROUTER_DASHBOARD_URL, OPENROUTER_CREDITS_URL,
    OPENROUTER_SETTINGS_URL, OPENROUTER_MODELS_URL,
    STARTUP_REG_KEY, STARTUP_REG_NAME, STARTUP_REG_LEGACY_NAME,
)


class TrayIcon(QSystemTrayIcon):
    """System tray icon with dynamic gauge rendering and rich context menu."""

    toggle_dashboard = Signal()
    refresh_requested = Signal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._credit_percent = 1.0
        self._credit_text = "$0.00"
        self._last_warning = None
        self._error_state = False

        self._update_icon(1.0)
        self.setToolTip(f"{APP_NAME}\nLoading...")

        self._migrate_legacy_startup_entry()
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _migrate_legacy_startup_entry(self):
        """If a pre-rebrand 'OpenRouterPulse' Run entry exists, copy it
        to the new name and remove the old one.  Preserves the user's
        'Start with Windows' preference across the rebrand.
        """
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY, 0,
                winreg.KEY_READ | winreg.KEY_WRITE,
            )
            try:
                value, _ = winreg.QueryValueEx(key, STARTUP_REG_LEGACY_NAME)
            except (FileNotFoundError, OSError):
                value = None
            if value is not None:
                # Only set new entry if not already there (don't clobber a
                # newer one the user has already configured).
                try:
                    winreg.QueryValueEx(key, STARTUP_REG_NAME)
                except (FileNotFoundError, OSError):
                    winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, value)
                try:
                    winreg.DeleteValue(key, STARTUP_REG_LEGACY_NAME)
                except (FileNotFoundError, OSError):
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[tray] legacy startup migrate: {e}")

    # ------------------------------------------------------------------
    #  Dynamic icon drawing
    # ------------------------------------------------------------------

    def _update_icon(self, percent, error=False):
        size = 64
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx, cy = size / 2, size / 2
        radius = size / 2 - 6
        arc_w = 7

        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        bg_pen = QPen(QColor(50, 50, 80), arc_w,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(bg_pen)
        start = 225 * 16
        span = -270 * 16
        painter.drawArc(rect, start, span)

        if error:
            color = Colors.RED
        else:
            color = Colors.credit_color(percent)

        value_span = int(-270 * 16 * max(0, min(1, percent)))

        glow_c = QColor(color)
        glow_c.setAlpha(50)
        painter.setPen(QPen(glow_c, arc_w + 4,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, start, value_span)

        painter.setPen(QPen(color, arc_w,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, start, value_span)

        painter.setPen(QColor(240, 240, 255))
        f = Fonts.body()
        f.setPointSize(12)
        f.setWeight(f.Weight.Bold)
        painter.setFont(f)
        label = "!" if error else "OR"
        painter.drawText(QRectF(0, 0, size, size),
                         Qt.AlignmentFlag.AlignCenter, label)

        painter.end()

        pixmap = QPixmap.fromImage(img)
        self.setIcon(QIcon(pixmap))

    # ------------------------------------------------------------------
    #  Context Menu
    # ------------------------------------------------------------------

    def _build_menu(self):
        # We build the menu but do NOT call setContextMenu() — Qt's
        # default positioning puts the menu's TOP-LEFT at the click,
        # which gets truncated by the taskbar.  Instead we intercept
        # the Context activation reason and exec the menu manually with
        # its BOTTOM-RIGHT anchored to the tray icon's top-left.
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #1c1c32;
                color: #f0f0ff;
                border: 1px solid #323250;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2a2a50;
                color: #00d2ff;
            }
            QMenu::separator {
                height: 1px;
                background: #323250;
                margin: 4px 8px;
            }
        """)

        self._header_action = QAction(f"{APP_NAME}", menu)
        self._header_action.setEnabled(False)
        menu.addAction(self._header_action)

        self._balance_action = QAction("Balance: loading...", menu)
        self._balance_action.setEnabled(False)
        menu.addAction(self._balance_action)

        self._burn_action = QAction("", menu)
        self._burn_action.setEnabled(False)
        self._burn_action.setVisible(False)
        menu.addAction(self._burn_action)

        menu.addSeparator()

        dash_action = QAction("Open Dashboard", menu)
        dash_action.triggered.connect(self.toggle_dashboard.emit)
        menu.addAction(dash_action)

        refresh_action = QAction("Refresh Now", menu)
        refresh_action.triggered.connect(self.refresh_requested.emit)
        menu.addAction(refresh_action)

        menu.addSeparator()

        links_menu = QMenu("Quick Links", menu)
        links_menu.setStyleSheet(menu.styleSheet())

        for label, url in (
            ("OpenRouter Dashboard", OPENROUTER_DASHBOARD_URL),
            ("Add Credits", OPENROUTER_CREDITS_URL),
            ("Browse Models", OPENROUTER_MODELS_URL),
            ("API Keys", OPENROUTER_SETTINGS_URL),
        ):
            act = QAction(label, links_menu)
            act.triggered.connect(lambda _checked, u=url: webbrowser.open(u))
            links_menu.addAction(act)
        menu.addMenu(links_menu)

        menu.addSeparator()

        open_settings = QAction("Open Settings File...", menu)
        open_settings.triggered.connect(self._open_settings_file)
        menu.addAction(open_settings)

        self._startup_action = QAction("Start with Windows", menu)
        self._startup_action.setCheckable(True)
        self._startup_action.setChecked(self._is_startup_enabled())
        self._startup_action.triggered.connect(self._toggle_startup)
        menu.addAction(self._startup_action)

        menu.addSeparator()

        exit_action = QAction("Exit", menu)
        exit_action.triggered.connect(QApplication.quit)
        menu.addAction(exit_action)

        self._menu = menu  # exec'd manually in _on_activated

    def _open_settings_file(self):
        try:
            from settings import settings_path
            p = str(settings_path())
            # Prefer the user's default editor association
            os.startfile(p)
        except Exception as e:
            print(f"[Tray] open settings error: {e}")

    # ------------------------------------------------------------------
    #  Public update methods
    # ------------------------------------------------------------------

    def update_credit_info(self, key_info, history=None):
        # Clear error state on successful update
        self._error_state = False
        percent = key_info.credit_percent
        self._credit_percent = percent
        self._update_icon(percent, error=False)

        remaining = key_info.remaining
        rate_hourly = None
        if history is not None:
            rate_hourly = history.burn_rate_per_hour(3600)
            if rate_hourly is None:
                rate_hourly = history.burn_rate_per_hour(86400)

        if remaining is not None:
            self._credit_text = f"${remaining:.2f}"
            self._balance_action.setText(f"Balance: ${remaining:.2f}")
            tip_lines = [
                APP_NAME,
                f"Balance: ${remaining:.2f}",
                f"Today: ${key_info.usage_daily:.2f}",
            ]
            if rate_hourly is not None and rate_hourly > 0:
                tip_lines.append(f"Recent: ${rate_hourly:.3f}/hr")
                self._burn_action.setText(f"Burn: ${rate_hourly:.3f}/hr")
                self._burn_action.setVisible(True)
            else:
                self._burn_action.setVisible(False)
            if self._settings and self._settings.autotopup_enabled:
                tip_lines.append(
                    f"Auto top-up: +${self._settings.auto_topup_amount:g} @ ${self._settings.auto_topup_threshold:g}"
                )
            self.setToolTip("\n".join(tip_lines))

            # Threshold alerts
            warn = self._settings.balance_warning if self._settings else 5.0
            crit = self._settings.balance_critical if self._settings else 1.0
            if remaining < crit and self._last_warning != "critical":
                self._last_warning = "critical"
                self.showMessage(
                    "Credits Critical!",
                    f"Only ${remaining:.2f} remaining.",
                    QSystemTrayIcon.MessageIcon.Critical,
                    5000,
                )
            elif remaining < warn and self._last_warning is None:
                self._last_warning = "warning"
                self.showMessage(
                    "Low Credits",
                    f"${remaining:.2f} remaining.",
                    QSystemTrayIcon.MessageIcon.Warning,
                    4000,
                )
            elif remaining >= warn:
                # Reset latch so we can warn again on next drop
                self._last_warning = None
        else:
            self._balance_action.setText(f"Usage: ${key_info.usage:.2f}")
            self.setToolTip(f"{APP_NAME}\nUsage today: ${key_info.usage_daily:.2f}")

    def set_error(self, msg):
        self._error_state = True
        self._update_icon(self._credit_percent, error=True)
        # Keep balance line; add error suffix to tooltip
        cur = self.toolTip()
        if "API Error" not in cur:
            self.setToolTip(cur + f"\n⚠ API Error: {msg[:60]}")

    def clear_error(self):
        if self._error_state:
            self._error_state = False
            self._update_icon(self._credit_percent, error=False)

    # ------------------------------------------------------------------
    #  Startup management
    # ------------------------------------------------------------------

    def _is_startup_enabled(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, STARTUP_REG_NAME)
            winreg.CloseKey(key)
            return True
        except (FileNotFoundError, OSError):
            return False

    def _toggle_startup(self, checked):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY, 0, winreg.KEY_WRITE)
            if checked:
                exe = sys.executable
                script = sys.argv[0] if sys.argv else ""
                cmd = f'"{exe}" "{script}"'
                winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_REG_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Tray] Startup toggle error: {e}")

    # ------------------------------------------------------------------
    #  Click handling
    # ------------------------------------------------------------------

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self._show_menu_anchored()
        elif reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_dashboard.emit()

    def _show_menu_anchored(self):
        """Show the context menu with its bottom-right at the tray icon's
        top-left, clamped to the screen.  Matches how the dashboard
        anchors to the tray and avoids being truncated by the taskbar.
        """
        # Force the menu to compute its size before we position it
        self._menu.adjustSize()
        size = self._menu.sizeHint()

        icon_rect = self.geometry()
        # Fall back to cursor position if the OS doesn't report icon rect
        if icon_rect.isNull() or icon_rect.width() == 0:
            anchor = QCursor.pos()
        else:
            anchor = QPoint(icon_rect.left(), icon_rect.top())

        x = anchor.x() - size.width()
        y = anchor.y() - size.height()

        # Clamp to the screen that contains the tray icon
        screen = QApplication.screenAt(anchor)
        if screen is not None:
            avail = screen.availableGeometry()
            x = max(avail.left(), min(x, avail.right() - size.width()))
            y = max(avail.top(), min(y, avail.bottom() - size.height()))

        self._menu.exec(QPoint(x, y))
