"""
OpenRouter Pulse - Dashboard Window
The main popup panel that appears from the system tray.
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import webbrowser
import time
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QPushButton, QGraphicsDropShadowEffect,
    QSizePolicy, QApplication, QGridLayout,
)
from PySide6.QtCore import (
    Qt, QTimer, QEasingCurve, QPoint, QSize, Signal, QRectF, QEvent,
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPainterPath, QCursor,
    QLinearGradient, QIcon, QPixmap, QImage,
)

from theme import Colors, Fonts, STYLESHEET
from config import (
    DASHBOARD_WIDTH, DASHBOARD_MIN_HEIGHT, DASHBOARD_MAX_HEIGHT,
    APP_NAME, APP_VERSION,
    OPENROUTER_DASHBOARD_URL, OPENROUTER_CREDITS_URL, OPENROUTER_MODELS_URL,
)
from widgets import (
    ArcGauge, StatCard, SectionHeader, BurnRateBar, GradientStrip,
    ErrorBanner, TimelineChart,
)


def _fmt_duration(days):
    if days is None or days <= 0:
        return "--"
    if days < 1 / 24:
        return f"{int(days * 1440)} min"
    if days < 1:
        return f"{int(days * 24)} hr"
    if days < 30:
        return f"{days:.0f} day{'s' if days >= 1.5 else ''}"
    if days < 365:
        return f"{days / 30:.1f} mo"
    return f"{days / 365:.1f} yr"


def _fmt_money(x):
    if x is None:
        return "--"
    if x >= 1000:
        return f"${x:,.0f}"
    return f"${x:.2f}"


class IconButton(QPushButton):
    """Small icon-style button."""
    def __init__(self, text, tooltip="", parent=None):
        super().__init__(text, parent)
        self.setFixedSize(28, 28)
        self.setToolTip(tooltip)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet("""
            QPushButton {
                background: #242442;
                color: #a0a0c8;
                border: 1px solid #323250;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #2e2e52;
                color: #00d2ff;
                border-color: #00d2ff;
            }
            QPushButton:pressed {
                background: #1a1a30;
            }
        """)


class LinkButton(QPushButton):
    def __init__(self, text, url, parent=None):
        super().__init__(text, parent)
        self._url = url
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet("""
            QPushButton {
                background: #242442;
                color: #00d2ff;
                border: 1px solid #323250;
                border-radius: 6px;
                padding: 6px 12px;
                font-family: "Segoe UI";
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #2e2e52;
                border-color: #00d2ff;
                color: #7b2ff7;
            }
        """)
        self.clicked.connect(lambda: webbrowser.open(self._url))


class CardFrame(QWidget):
    """A card-style container with rounded background."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QBrush(Colors.BG_CARD))
        pen = QPen(Colors.BORDER, 1)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()


class Dashboard(QWidget):
    """The main dashboard popup window."""

    refresh_requested = Signal()

    def __init__(self, history=None, settings=None, parent=None):
        super().__init__(parent)
        self.setObjectName("DashboardWindow")

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.setFixedWidth(DASHBOARD_WIDTH)
        self.setMinimumHeight(DASHBOARD_MIN_HEIGHT)
        self.setMaximumHeight(DASHBOARD_MAX_HEIGHT)

        self._tray_icon = None
        self._history = history
        self._settings = settings
        self._last_key_info = None

        self._build_ui()

        # Click-outside-to-dismiss via foreground-window polling.  See
        # _check_outside_click for the why and how.
        self._dismiss_enabled = bool(
            self._settings and getattr(self._settings, "dismiss_on_focus_loss", False)
        )
        self._outside_click_timer = QTimer(self)
        self._outside_click_timer.timeout.connect(self._check_outside_click)
        self._show_foreground = None

    # ------------------------------------------------------------------
    #  UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        self._container = QFrame(self)
        self._container.setObjectName("DashboardWindow")
        self._container.setStyleSheet(STYLESHEET)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 4)
        self._container.setGraphicsEffect(shadow)

        inner = QVBoxLayout(self._container)
        inner.setContentsMargins(0, 0, 0, 12)
        inner.setSpacing(0)

        self.gradient_strip = GradientStrip(self._container)
        inner.addWidget(self.gradient_strip)

        scroll = QScrollArea(self._container)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        self._content = QVBoxLayout(content_widget)
        self._content.setContentsMargins(16, 8, 16, 8)
        self._content.setSpacing(10)

        self._build_header()
        self._build_error_banner()
        self._build_gauge_section()
        self._build_usage_section()
        self._build_burn_rate()
        self._build_quick_links()

        self._content.addStretch()

        scroll.setWidget(content_widget)
        inner.addWidget(scroll)

        root.addWidget(self._container)

    def _build_header(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        title = QLabel(APP_NAME)
        title.setFont(Fonts.heading())
        title.setStyleSheet("color: #f0f0ff;")
        row.addWidget(title)

        ver = QLabel(f"v{APP_VERSION}")
        ver.setFont(Fonts.tiny())
        ver.setStyleSheet("color: #64648c;")
        row.addWidget(ver)

        row.addStretch()

        self._refresh_btn = IconButton("↻", "Refresh now", self)
        self._refresh_btn.clicked.connect(self._on_refresh)
        row.addWidget(self._refresh_btn)

        self._close_btn = IconButton("✕", "Close", self)
        self._close_btn.clicked.connect(self.hide)
        row.addWidget(self._close_btn)

        self._content.addLayout(row)

    def _build_error_banner(self):
        self.error_banner = ErrorBanner(self)
        self._content.addWidget(self.error_banner)

    def _build_gauge_section(self):
        self._content.addWidget(SectionHeader("Credit Balance"))

        gauge_row = QHBoxLayout()
        gauge_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gauge = ArcGauge(self)
        gauge_row.addWidget(self.gauge)
        self._content.addLayout(gauge_row)

        self._autotopup_label = QLabel("")
        self._autotopup_label.setFont(Fonts.tiny())
        self._autotopup_label.setStyleSheet("color: #00d2ff;")
        self._autotopup_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content.addWidget(self._autotopup_label)
        self._update_autotopup_label()

    def _update_autotopup_label(self):
        if self._settings and self._settings.autotopup_enabled:
            t = self._settings.auto_topup_threshold
            a = self._settings.auto_topup_amount
            self._autotopup_label.setText(
                f"⚡  Auto top-up: +${a:g} when balance drops below ${t:g}"
            )
            self._autotopup_label.setVisible(True)
        else:
            self._autotopup_label.setVisible(False)

    def _build_usage_section(self):
        self._content.addWidget(SectionHeader("Usage"))

        self.timeline = TimelineChart(self)
        topup_thr = self._settings.auto_topup_threshold if self._settings else 0.0
        self.timeline.set_data([], [], topup_thr, "last 24h")
        self._content.addWidget(self.timeline)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.kpi_today = StatCard("Today")
        self.kpi_monthly = StatCard("Projected / mo")
        grid.addWidget(self.kpi_today, 0, 0)
        grid.addWidget(self.kpi_monthly, 0, 1)
        self._content.addLayout(grid)

    def _build_burn_rate(self):
        self._content.addWidget(SectionHeader("Burn Rate"))

        burn_card = CardFrame(self)
        burn_card.setFixedHeight(60)
        burn_layout = QVBoxLayout(burn_card)
        burn_layout.setContentsMargins(14, 6, 14, 6)

        self.burn_rate_bar = BurnRateBar(self)
        burn_layout.addWidget(self.burn_rate_bar)

        self._content.addWidget(burn_card)

    def _build_quick_links(self):
        self._content.addWidget(SectionHeader("Quick Links"))

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(LinkButton("Dashboard", OPENROUTER_DASHBOARD_URL))
        row.addWidget(LinkButton("Add Credits", OPENROUTER_CREDITS_URL))
        row.addWidget(LinkButton("Models", OPENROUTER_MODELS_URL))
        self._content.addLayout(row)

    # ------------------------------------------------------------------
    #  Data updates
    # ------------------------------------------------------------------

    def update_key_info(self, key_info):
        self._last_key_info = key_info

        remaining = key_info.remaining
        total = key_info.total_credits
        percent = key_info.credit_percent

        if remaining is not None:
            amount_text = f"${remaining:.2f}"
            total_text = f"/ ${total:.2f}" if total > 0 else ""
        else:
            amount_text = "N/A"
            total_text = f"Used: ${key_info.usage:.2f}"
            percent = 1.0

        rate_hourly, rate_daily, monthly_proj, forecast, rate_source = (
            self._smart_forecast(key_info)
        )
        self.gauge.set_value(percent, amount_text, total_text, forecast)

        self._update_autotopup_label()

        tip = self._build_forecast_tooltip(
            key_info, rate_hourly, rate_daily, monthly_proj, rate_source
        )
        self.gauge.setToolTip(tip)
        self.burn_rate_bar.setToolTip(tip)
        self._autotopup_label.setToolTip(tip)

        if self._history is not None:
            window_seconds = 24 * 3600
            series = self._history.balance_series(window_seconds)
            topups = self._history.topup_events(window_seconds)
            topup_thr = self._settings.auto_topup_threshold if self._settings else 0.0
            self.timeline.set_data(series, topups, topup_thr, "last 24h")

        self.kpi_today.set_value(f"${key_info.usage_daily:.2f}", "spent today")
        if monthly_proj is not None and monthly_proj > 0:
            self.kpi_monthly.set_value(
                _fmt_money(monthly_proj),
                "based on recent burn",
            )
        else:
            self.kpi_monthly.set_value(
                _fmt_money(key_info.usage_monthly),
                "this month so far",
            )

        pct_used = 1.0 - percent if remaining is not None and total > 0 else 0.0
        if rate_hourly is not None and rate_hourly > 0:
            rate_text = f"${rate_hourly:.3f}/hr · ${rate_daily:.2f}/day"
        else:
            rate_text = "Insufficient data"
        self.burn_rate_bar.set_data(pct_used, forecast, rate_text)

    def _smart_forecast(self, key_info):
        """Returns (rate_hourly, rate_daily, monthly_proj, forecast_text, rate_source)."""
        rate_hourly = None
        rate_source = None
        if self._history is not None:
            rate_hourly = self._history.burn_rate_per_hour(3600)
            if rate_hourly is not None:
                rate_source = "last 1h of history"
            else:
                rate_hourly = self._history.burn_rate_per_hour(86400)
                if rate_hourly is not None:
                    rate_source = "last 24h of history"

        if rate_hourly is None or rate_hourly == 0:
            rate_hourly = key_info.burn_rate_hourly
            rate_source = "today's API total" if rate_hourly > 0 else None

        rate_daily = rate_hourly * 24 if rate_hourly else 0
        monthly_proj = rate_daily * 30 if rate_daily else None
        remaining = key_info.remaining

        forecast = "--"
        if self._settings and self._settings.autotopup_enabled:
            thr = self._settings.auto_topup_threshold
            amt = self._settings.auto_topup_amount
            if remaining is None:
                forecast = f"Auto-top-up at ${thr:g}"
            elif remaining <= thr:
                forecast = f"Top-up pending (+${amt:g})"
            elif rate_daily > 0:
                days_to_topup = (remaining - thr) / rate_daily
                forecast = f"Next top-up in {_fmt_duration(days_to_topup)}"
            else:
                forecast = f"Auto-top-up at ${thr:g}"
        else:
            if remaining is not None and rate_daily > 0:
                days = remaining / rate_daily
                forecast = f"Depletes in {_fmt_duration(days)}"
            elif rate_daily == 0:
                forecast = "No recent usage"

        return rate_hourly, rate_daily, monthly_proj, forecast, rate_source

    def _build_forecast_tooltip(self, key_info, rate_hourly, rate_daily,
                                 monthly_proj, rate_source):
        lines = []
        if rate_source:
            lines.append(f"Burn rate source: {rate_source}")
        else:
            lines.append("Burn rate: not enough data yet")
        if rate_hourly:
            lines.append(f"Rate: ${rate_hourly:.4f}/hr · ${rate_daily:.2f}/day")
        if monthly_proj:
            lines.append(f"30-day projection: ${monthly_proj:.2f}")
        rem = key_info.remaining
        if rem is not None:
            lines.append(f"Current balance: ${rem:.2f}")
        if self._settings and self._settings.autotopup_enabled:
            thr = self._settings.auto_topup_threshold
            amt = self._settings.auto_topup_amount
            lines.append(
                f"Auto-top-up: +${amt:g} when balance < ${thr:g} (settings.json)"
            )
            if rem is not None and rate_daily > 0:
                days_to_topup = max(0, (rem - thr) / rate_daily)
                lines.append(
                    f"-> (balance - threshold) / rate = "
                    f"(${rem:.2f} - ${thr:g}) / ${rate_daily:.2f}/day = "
                    f"{_fmt_duration(days_to_topup)} until next top-up"
                )
        else:
            lines.append("Auto-top-up: disabled (configure in settings.json)")
            if rem is not None and rate_daily > 0:
                days = rem / rate_daily
                lines.append(
                    f"-> balance / rate = ${rem:.2f} / ${rate_daily:.2f}/day = "
                    f"{_fmt_duration(days)} until depletion"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    #  Error banner
    # ------------------------------------------------------------------

    def show_error(self, message):
        self.error_banner.set_message(message)

    def clear_error(self):
        self.error_banner.set_message("")

    # ------------------------------------------------------------------
    #  Positioning and show/hide
    # ------------------------------------------------------------------

    def set_tray_icon(self, tray_icon):
        self._tray_icon = tray_icon

    def show_near_tray(self):
        screen = QApplication.primaryScreen()
        if not screen:
            self._show_no_activate()
            return

        avail = screen.availableGeometry()
        margin = 12

        popup_w = self.width()
        popup_h = max(self.minimumHeight(), self.sizeHint().height())
        popup_h = min(popup_h, avail.height() - 2 * margin)
        self.setFixedHeight(popup_h)

        icon_rect = None
        if self._tray_icon is not None:
            icon_rect = self._tray_icon.geometry()
            if (icon_rect.isNull() or not icon_rect.isValid()
                    or icon_rect.width() == 0 or icon_rect.height() == 0):
                icon_rect = None

        if icon_rect is not None:
            tray_screen = QApplication.screenAt(icon_rect.center())
            if tray_screen is not None:
                avail = tray_screen.availableGeometry()
            x = icon_rect.left()
            y = icon_rect.top() - popup_h
            if x + popup_w > avail.right():
                x = avail.right() - popup_w + 1
            if x < avail.left():
                x = avail.left()
            if y < avail.top():
                y = icon_rect.bottom()
                if y + popup_h > avail.bottom():
                    y = avail.top()
        else:
            cursor_pos = QCursor.pos()
            target_screen = QApplication.screenAt(cursor_pos)
            if target_screen:
                avail = target_screen.availableGeometry()
            x = avail.right() - popup_w - margin + 1
            y = avail.bottom() - popup_h - margin + 1

        self.move(x, y)
        self._show_no_activate()

    def _show_no_activate(self):
        self.setVisible(True)
        hwnd = int(self.winId())
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        try:
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass
        HWND_TOPMOST = -1
        SWP_NOACTIVATE = 0x0010
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        SWP_FRAMECHANGED = 0x0020
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_FRAMECHANGED,
        )

    def toggle(self):
        if self.isVisible():
            self.hide()
            if self._dismiss_enabled:
                self._outside_click_timer.stop()
                self._show_foreground = None
        else:
            self.show_near_tray()
            if self._dismiss_enabled:
                self._show_foreground = None
                QTimer.singleShot(
                    250, lambda: self._outside_click_timer.start(150)
                )

    # ------------------------------------------------------------------
    #  Click-outside-to-dismiss
    # ------------------------------------------------------------------

    def _check_outside_click(self):
        if not self.isVisible():
            return
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            return
        if self._show_foreground is None:
            self._show_foreground = hwnd
            return
        if hwnd == self._show_foreground:
            return
        try:
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(
                wintypes.HWND(hwnd), ctypes.byref(pid)
            )
            if pid.value == os.getpid():
                self._show_foreground = hwnd
                return
        except Exception:
            pass
        self.hide()

    # ------------------------------------------------------------------
    #  Refresh
    # ------------------------------------------------------------------

    def _on_refresh(self):
        self.refresh_requested.emit()

    def paintEvent(self, event):
        pass
