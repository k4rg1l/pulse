"""
OpenRouter Pulse - Custom Widgets
Hand-drawn gauges, sparklines, stat cards, status badges.
"""
import math
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QGraphicsDropShadowEffect, QSizePolicy, QLineEdit,
    QScrollArea, QFrame, QApplication,
)
from PySide6.QtGui import QCursor
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, Property, QEasingCurve,
    QRectF, QPointF, QPoint, QRect, Signal, QSize, QEvent,
)
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QConicalGradient,
    QRadialGradient, QLinearGradient, QPainterPath, QFont,
    QFontMetrics,
)

from theme import Colors, Fonts


# ---------------------------------------------------------------------------
#  Animated Arc Gauge
# ---------------------------------------------------------------------------
def _safe_paint(widget):
    """Return True if widget is safe to paint (has valid size)."""
    return widget.width() > 0 and widget.height() > 0


class ArcGauge(QWidget):
    """A large, animated circular arc gauge for credit balance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent = 0.0
        self._display_percent = 0.0
        self._amount_text = "$0.00"
        self._total_text = "/ $0.00"
        self._subtitle_text = ""
        self.setMinimumSize(200, 200)
        self.setMaximumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._anim = QPropertyAnimation(self, b"display_percent")
        self._anim.setDuration(800)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_display_percent(self):
        return self._display_percent

    def set_display_percent(self, val):
        self._display_percent = val
        self.update()

    display_percent = Property(float, get_display_percent, set_display_percent)

    def set_value(self, percent, amount_text, total_text, subtitle=""):
        self._percent = max(0.0, min(1.0, percent))
        self._amount_text = amount_text
        self._total_text = total_text
        self._subtitle_text = subtitle
        self._anim.stop()
        self._anim.setStartValue(self._display_percent)
        self._anim.setEndValue(self._percent)
        self._anim.start()

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 16
        arc_width = 10

        # Background arc (track)
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        start_angle = 225 * 16
        span_angle = -270 * 16

        bg_pen = QPen(Colors.BORDER, arc_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(bg_pen)
        painter.drawArc(rect, start_angle, span_angle)

        # Foreground arc (value)
        color = Colors.credit_color(self._display_percent)
        value_span = int(-270 * 16 * self._display_percent)

        # Glow effect
        glow_color = QColor(color)
        glow_color.setAlpha(40)
        glow_pen = QPen(glow_color, arc_width + 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        painter.drawArc(rect, start_angle, value_span)

        # Main arc
        fg_pen = QPen(color, arc_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(fg_pen)
        painter.drawArc(rect, start_angle, value_span)

        # Center text - amount
        painter.setPen(Colors.TEXT_PRIMARY)
        painter.setFont(Fonts.mono_large())
        painter.drawText(QRectF(0, cy - 36, w, 40),
                         Qt.AlignmentFlag.AlignCenter, self._amount_text)

        # Total text
        painter.setPen(Colors.TEXT_MUTED)
        painter.setFont(Fonts.mono_small())
        painter.drawText(QRectF(0, cy + 4, w, 20),
                         Qt.AlignmentFlag.AlignCenter, self._total_text)

        # Subtitle
        if self._subtitle_text:
            painter.setPen(Colors.TEXT_SECONDARY)
            painter.setFont(Fonts.tiny())
            painter.drawText(QRectF(0, cy + 24, w, 18),
                             Qt.AlignmentFlag.AlignCenter, self._subtitle_text)

        # Percentage badge at bottom of arc
        pct_text = f"{int(self._display_percent * 100)}%"
        painter.setPen(color)
        painter.setFont(Fonts.label())
        painter.drawText(QRectF(0, h - 28, w, 20),
                         Qt.AlignmentFlag.AlignCenter, pct_text)

        painter.end()


# ---------------------------------------------------------------------------
#  Sparkline Chart
# ---------------------------------------------------------------------------
class SparklineWidget(QWidget):
    """A mini sparkline chart with gradient fill."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []
        self._color = Colors.CYAN
        self.setMinimumSize(80, 30)
        self.setMaximumHeight(35)

    def set_data(self, data, color=None):
        self._data = data if data else []
        if color:
            self._color = color
        self.update()

    def paintEvent(self, event):
        if len(self._data) < 2 or not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        pad = 2

        data = self._data
        mn = min(data)
        mx = max(data)
        rng = mx - mn if mx != mn else 1.0

        points = []
        for i, v in enumerate(data):
            x = pad + (w - 2 * pad) * i / (len(data) - 1)
            y = h - pad - (h - 2 * pad) * (v - mn) / rng
            points.append(QPointF(x, y))

        # Fill gradient
        path = QPainterPath()
        path.moveTo(points[0].x(), h)
        for p in points:
            path.lineTo(p)
        path.lineTo(points[-1].x(), h)
        path.closeSubpath()

        grad = QLinearGradient(0, 0, 0, h)
        fill_color = QColor(self._color)
        fill_color.setAlpha(60)
        grad.setColorAt(0, fill_color)
        fill_color.setAlpha(5)
        grad.setColorAt(1, fill_color)
        painter.fillPath(path, QBrush(grad))

        # Line
        pen = QPen(self._color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        line_path = QPainterPath()
        line_path.moveTo(points[0])
        for p in points[1:]:
            line_path.lineTo(p)
        painter.drawPath(line_path)

        # End dot
        painter.setBrush(QBrush(self._color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(points[-1], 3, 3)

        painter.end()


# ---------------------------------------------------------------------------
#  Stat Card
# ---------------------------------------------------------------------------
class StatCard(QWidget):
    """A glassmorphic stat card with title, value, and optional sparkline."""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self._title = title
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        # Title label
        self.title_label = QLabel(title.upper())
        self.title_label.setFont(Fonts.label())
        self.title_label.setStyleSheet("color: #a0a0c8;")
        layout.addWidget(self.title_label)

        # Value
        self.value_label = QLabel("--")
        self.value_label.setFont(Fonts.mono_medium())
        self.value_label.setStyleSheet("color: #f0f0ff;")
        layout.addWidget(self.value_label)

        # Sub value
        self.sub_label = QLabel("")
        self.sub_label.setFont(Fonts.tiny())
        self.sub_label.setStyleSheet("color: #64648c;")
        layout.addWidget(self.sub_label)

        # Sparkline
        self.sparkline = SparklineWidget(self)
        layout.addWidget(self.sparkline)
        self.sparkline.hide()

    def set_value(self, value, sub="", sparkline_data=None, color=None):
        self.value_label.setText(value)
        self.sub_label.setText(sub)
        if sparkline_data and len(sparkline_data) >= 2:
            self.sparkline.set_data(sparkline_data, color)
            self.sparkline.show()
        else:
            self.sparkline.hide()

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)

        # Card background
        painter.fillPath(path, QBrush(Colors.BG_CARD))

        # Border
        pen = QPen(Colors.BORDER, 1)
        painter.setPen(pen)
        painter.drawPath(path)

        painter.end()


# ---------------------------------------------------------------------------
#  Status Badge
# ---------------------------------------------------------------------------
class StatusBadge(QWidget):
    """A small status indicator with pulsing dot."""

    def __init__(self, label="", parent=None):
        super().__init__(parent)
        self._label = label
        self._status = "unknown"
        self._pulse = 0.0
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Measure with the actual rendering font; 22px reserves space for
        # dot+gap on the left, 6px right padding.
        text_w = QFontMetrics(Fonts.body()).horizontalAdvance(label)
        self.setFixedWidth(22 + text_w + 6)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._animate_pulse)
        self._pulse_timer.start(500)
        self._pulse_dir = 1

    def set_status(self, status):
        self._status = status
        self.update()

    def _animate_pulse(self):
        if not self.isVisible():
            return
        self._pulse += 0.15 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse_dir = -1
        elif self._pulse <= 0.0:
            self._pulse_dir = 1
        self.update()

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._status == "operational":
            color = Colors.GREEN
        elif self._status == "degraded":
            color = Colors.YELLOW
        else:
            color = Colors.TEXT_MUTED

        # Pulsing glow
        if self._status == "operational":
            glow = QColor(color)
            glow.setAlpha(int(40 * self._pulse))
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(10, 11), 6, 6)

        # Dot
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(10, 11), 4, 4)

        # Label
        painter.setPen(Colors.TEXT_SECONDARY)
        painter.setFont(Fonts.body())
        painter.drawText(QRectF(22, 0, self.width() - 24, self.height()),
                         Qt.AlignmentFlag.AlignVCenter, self._label)

        painter.end()


# ---------------------------------------------------------------------------
#  Section Header
# ---------------------------------------------------------------------------
class SectionHeader(QWidget):
    """A styled section header with optional right-side text.

    Optionally collapsible: call set_collapsible(True) to show a chevron
    on the left and make the header clickable. The header itself doesn't
    hide anything; it just emits `clicked` so the owner can toggle
    whatever child widgets belong to the section.
    """

    clicked = Signal()

    def __init__(self, title, right_text="", parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._collapsible = False
        self._collapsed = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)

        self.chevron = QLabel("")
        # Larger + bolder than the title text so the affordance reads
        # clearly. Letter-spaced label font garbles geometry glyphs, so
        # use a plain Segoe UI here.
        chev_font = QFont("Segoe UI", 11)
        chev_font.setWeight(QFont.Weight.Bold)
        self.chevron.setFont(chev_font)
        self.chevron.setStyleSheet("color: #a0a0c8;")
        self.chevron.setFixedWidth(16)
        # Don't grab clicks — let them bubble to SectionHeader.mousePressEvent
        self.chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.chevron)

        left = QLabel(title.upper())
        left.setFont(Fonts.label())
        left.setStyleSheet("color: #a0a0c8;")
        layout.addWidget(left)

        layout.addStretch()

        self.right_label = QLabel(right_text)
        self.right_label.setFont(Fonts.tiny())
        self.right_label.setStyleSheet("color: #64648c;")
        layout.addWidget(self.right_label)

    def set_collapsible(self, collapsible: bool):
        self._collapsible = collapsible
        if collapsible:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self._refresh_chevron()
        else:
            self.unsetCursor()
            self.chevron.setText("")

    def set_collapsed(self, collapsed: bool):
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._refresh_chevron()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _refresh_chevron(self):
        # ▾ when expanded (pointing down → content below visible)
        # ▸ when collapsed (pointing right → click to expand)
        self.chevron.setText("▸" if self._collapsed else "▾")

    def mousePressEvent(self, event):
        if self._collapsible and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
#  Burn Rate Bar
# ---------------------------------------------------------------------------
class BurnRateBar(QWidget):
    """Visual bar showing credit depletion timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent_used = 0.0
        self._days_text = ""
        self._rate_text = ""
        self.setFixedHeight(50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, percent_used, days_text, rate_text):
        self._percent_used = max(0.0, min(1.0, percent_used))
        self._days_text = days_text
        self._rate_text = rate_text
        self.update()

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        pad = 4

        # Rate text
        painter.setPen(Colors.TEXT_SECONDARY)
        painter.setFont(Fonts.body())
        painter.drawText(QRectF(pad, 0, w - 2 * pad, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._rate_text)

        # Days remaining
        painter.setPen(Colors.TEXT_ACCENT)
        painter.setFont(Fonts.body())
        painter.drawText(QRectF(pad, 0, w - 2 * pad, 18),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         self._days_text)

        # Bar background
        bar_y = 24
        bar_h = 8
        bar_rect = QRectF(pad, bar_y, w - 2 * pad, bar_h)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(bar_rect, 4, 4)
        painter.fillPath(bg_path, QBrush(Colors.BORDER))

        # Bar fill
        fill_w = (w - 2 * pad) * self._percent_used
        fill_rect = QRectF(pad, bar_y, fill_w, bar_h)
        fill_path = QPainterPath()
        fill_path.addRoundedRect(fill_rect, 4, 4)

        color = Colors.credit_color(1.0 - self._percent_used)
        grad = QLinearGradient(pad, 0, pad + fill_w, 0)
        grad.setColorAt(0, Colors.CYAN)
        grad.setColorAt(1, color)
        painter.fillPath(fill_path, QBrush(grad))

        # Labels
        painter.setPen(Colors.TEXT_MUTED)
        painter.setFont(Fonts.tiny())
        painter.drawText(QRectF(pad, bar_y + bar_h + 2, 60, 16),
                         Qt.AlignmentFlag.AlignLeft, "used")
        painter.drawText(QRectF(w - 60 - pad, bar_y + bar_h + 2, 60, 16),
                         Qt.AlignmentFlag.AlignRight, "remaining")

        painter.end()


# ---------------------------------------------------------------------------
#  Gradient Status Strip
# ---------------------------------------------------------------------------
class GradientStrip(QWidget):
    """Thin animated gradient strip at the top of the dashboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._offset = 0.0
        self._status_color = Colors.CYAN

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    def set_status(self, ok=True):
        self._status_color = Colors.CYAN if ok else Colors.RED

    def _tick(self):
        if not self.isVisible():
            return
        self._offset = (self._offset + 0.008) % 1.0
        self.update()

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        # Match the dashboard container's border-radius so the strip
        # doesn't poke out at the rounded top corners.  The 12px radius
        # exceeds our 3px height, but only the topmost slice of the
        # curve falls inside our geometry — and that's exactly what we
        # need to make the top edge follow the corner.
        radius = 12
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, 2 * radius + 4), radius, radius)
        painter.setClipPath(clip)

        grad = QLinearGradient(0, 0, w, 0)
        c1 = Colors.CYAN
        c2 = Colors.MAGENTA
        o = self._offset
        grad.setColorAt(0, c1 if o < 0.5 else c2)
        grad.setColorAt(o, c2 if o < 0.5 else c1)
        grad.setColorAt(1.0, c1 if o < 0.5 else c2)

        painter.fillRect(0, 0, w, h, QBrush(grad))
        painter.end()


# ---------------------------------------------------------------------------
#  Model List Item
# ---------------------------------------------------------------------------
class ModelListItem(QWidget):
    """Compact model entry for the model browser."""

    def __init__(self, model_info, parent=None):
        super().__init__(parent)
        self.model_info = model_info
        self.setFixedHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        m = self.model_info
        p = m.price_per_mtok_prompt
        c = getattr(m, 'price_per_mtok_completion', 0.0)
        is_free = p == 0 and c == 0

        # Reserve right column for price / badge
        right_w = 110
        name_area = QRectF(8, 0, w - right_w - 8, h)
        right_area = QRectF(w - right_w - 4, 0, right_w, h)

        # Name (elided to fit)
        painter.setPen(Colors.TEXT_PRIMARY)
        painter.setFont(Fonts.body())
        fm = QFontMetrics(Fonts.body())
        name = fm.elidedText(m.name, Qt.TextElideMode.ElideRight, int(name_area.width()))
        painter.drawText(name_area, Qt.AlignmentFlag.AlignVCenter, name)

        if is_free:
            # FREE pill, right-aligned in the price column
            badge_w, badge_h = 44, 16
            bx = right_area.right() - badge_w
            by = (h - badge_h) / 2
            badge_rect = QRectF(bx, by, badge_w, badge_h)
            badge_path = QPainterPath()
            badge_path.addRoundedRect(badge_rect, 8, 8)
            painter.fillPath(badge_path, QBrush(Colors.GREEN_DIM))
            painter.setPen(Colors.GREEN)
            painter.setFont(Fonts.label())
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "FREE")
        else:
            price_text = _format_price_pair(p, c)
            painter.setPen(Colors.TEXT_ACCENT)
            painter.setFont(Fonts.mono_small())
            painter.drawText(right_area,
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             price_text)

        # Bottom separator
        painter.setPen(QPen(Colors.BORDER, 0.5))
        painter.drawLine(8, h - 1, w - 8, h - 1)

        painter.end()


# ---------------------------------------------------------------------------
#  Provider info popup (click-toggle, dismisses on outside click)
# ---------------------------------------------------------------------------
class ProviderPopup(QWidget):
    """Floating panel shown when the user clicks the (i) icon on a pinned
    model. Uses an application-wide event filter so any click outside the
    popup dismisses it. Content is HTML rendered into a QLabel.
    """

    hidden = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Tool window so it stays on top, doesn't take focus, and isn't
        # listed in the taskbar / Alt+Tab.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        self._frame = QFrame(self)
        self._frame.setObjectName("ProviderPopupFrame")
        self._frame.setStyleSheet(
            "QFrame#ProviderPopupFrame {"
            "  background: #1c1c32;"
            "  border: 1px solid #00d2ff;"
            "  border-radius: 10px;"
            "}"
            "QLabel { color: #f0f0ff; font-family: 'Segoe UI'; font-size: 9pt; }"
        )
        shadow = QGraphicsDropShadowEffect(self._frame)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 4)
        self._frame.setGraphicsEffect(shadow)

        inner = QVBoxLayout(self._frame)
        inner.setContentsMargins(14, 10, 14, 10)

        self.label = QLabel("", self._frame)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        inner.addWidget(self.label)

        root.addWidget(self._frame)

        # Let the popup size to its content (the table is wider than a
        # fixed 340px). Bounded so it doesn't grow huge on a wide screen.
        self.setMinimumWidth(360)
        self.setMaximumWidth(560)
        self.label.setMinimumWidth(320)

        # App-wide event filter so any mouse press outside us dismisses.
        # Installed lazily on first show to avoid touching QApplication
        # during construction.
        self._filter_installed = False

    def show_beside(self, html: str, dashboard_rect, anchor_y: int):
        """Render `html` and position the popup OUTSIDE the dashboard
        window. Preferred: to the LEFT of the dashboard, with the popup's
        right edge at dashboard_rect.left() - GAP, vertically centered on
        anchor_y. Fallbacks: right of dashboard if no room on left; clamp
        to screen if neither fits.

        Args:
            html: rich-text content to render
            dashboard_rect: QRect of the dashboard window in global coords
            anchor_y: global y to center the popup on (usually icon's y)
        """
        self.label.setText(html)
        # adjustSize alone GROWS the widget for bigger content but never
        # shrinks it when content gets smaller (Qt caches the larger
        # minimum from the previous layout). Force-reset the inner label
        # and frame to their content's true sizeHint so a tall popup
        # collapses back down when the next model has fewer providers.
        self.label.adjustSize()
        self.label.resize(self.label.sizeHint())
        self._frame.adjustSize()
        self._frame.resize(self._frame.sizeHint())
        self.adjustSize()
        self.resize(self.sizeHint())
        size = self.size()

        GAP = 12

        # Find the screen containing the dashboard
        center_pt = dashboard_rect.center()
        screen = QApplication.screenAt(center_pt)
        avail = screen.availableGeometry() if screen else None

        # Preferred: popup's right edge sits at dashboard.left - GAP
        x_left_of_dash = dashboard_rect.left() - GAP - size.width()
        # Fallback: popup's left edge sits at dashboard.right + GAP
        x_right_of_dash = dashboard_rect.right() + GAP

        if avail is not None and x_left_of_dash >= avail.left() + 4:
            x = x_left_of_dash
        elif avail is not None and x_right_of_dash + size.width() <= avail.right() - 4:
            x = x_right_of_dash
        else:
            # Neither side fits — clamp to whichever edge has more room
            x = x_left_of_dash if x_left_of_dash > 0 else x_right_of_dash

        y = anchor_y - size.height() // 2

        if avail is not None:
            x = max(avail.left() + 4, min(x, avail.right() - size.width() - 4))
            y = max(avail.top() + 4, min(y, avail.bottom() - size.height() - 4))

        self.move(int(x), int(y))

        if not self._filter_installed:
            QApplication.instance().installEventFilter(self)
            self._filter_installed = True

        self.show()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress and self.isVisible():
            try:
                gp = event.globalPosition().toPoint()
            except AttributeError:
                gp = event.globalPos()
            if not self.geometry().contains(gp):
                self.hide()
                # Don't consume the event; let it propagate so the icon
                # click that closed us can still register (the dashboard
                # handler debounces to prevent immediate reopen).
        return False

    def hideEvent(self, event):
        self.hidden.emit()
        super().hideEvent(event)


# ---------------------------------------------------------------------------
#  Pinned Model Card (per-provider health)
# ---------------------------------------------------------------------------
class PinnedModelCard(QWidget):
    """One pinned model with its per-provider health rows.

    Width is whatever the parent gives us; height grows with provider count.
    All drawing happens in paintEvent for tight column alignment.

    An (i) icon in the top-right corner toggles a detail popup when
    clicked. The icon's hover state is rendered with a cyan halo. The
    popup itself is owned by the dashboard.
    """

    ROW_H = 22
    HEADER_H = 28
    PAD_X = 14
    PAD_Y = 8
    ICON_VISIBLE = 16   # rendered glyph
    ICON_HIT = 22       # hit area (slightly bigger for usability)

    info_clicked = Signal(str, QPointF)   # (model_id, global anchor pos)

    def __init__(self, model_id, parent=None):
        super().__init__(parent)
        self.model_id = model_id
        self._endpoints = None       # ModelEndpoints or None
        self._error = False
        self._loading = True
        self._best = None
        self._icon_hit_rect = QRectF()  # set in paintEvent
        self._icon_hover = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._update_height()

    def _update_height(self):
        rows = len(self._endpoints.endpoints) if self._endpoints else 1
        h = self.HEADER_H + max(1, rows) * self.ROW_H + self.PAD_Y * 2
        self.setFixedHeight(h)

    def set_endpoints(self, model_endpoints):
        """ModelEndpoints or None (None means load failed)."""
        self._endpoints = model_endpoints
        self._error = model_endpoints is None
        self._loading = False
        if model_endpoints is not None:
            self._best = model_endpoints.best_provider()
        else:
            self._best = None
        self._update_height()
        self.update()

    def provider_html(self) -> str:
        """Return the HTML content for the info popup (dashboard pulls
        this on icon click)."""
        if self._error:
            return (
                f"<b>Couldn't load {self.model_id}</b><br>"
                f"<span style='color:#a0a0c8;'>Will retry on the next refresh.</span>"
            )
        if self._loading or not self._endpoints:
            return (
                f"<b>{self.model_id}</b><br>"
                f"<span style='color:#a0a0c8;'>Loading provider data…</span>"
            )

        # Helpers for clean per-cell formatting
        def lat_cell(ep):
            ms = ep.latency_p50
            if ms is None:
                return "—"
            return f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"

        def up_cell(ep):
            return f"{ep.uptime:.0f}%" if ep.uptime is not None else "—"

        def tp_cell(ep):
            return f"{ep.throughput_p50:.0f} t/s" if ep.throughput_p50 else "—"

        def ctx_cell(ep):
            if not ep.context_length:
                return "—"
            if ep.context_length >= 1_000_000:
                return f"{ep.context_length // 1_000_000}M"
            return f"{ep.context_length // 1000}k"

        # Use nowrap on provider name so long region tags
        # (e.g. "Amazon Bedrock · eu-west-1") don't wrap and break row alignment.
        # Tight cell padding keeps rows visually compact.
        rows = []
        for ep in self._endpoints.endpoints:
            region = ""
            if ep.tag and "/" in ep.tag:
                region = ep.tag.split("/", 1)[1]
            name = ep.provider_name + (f" · {region}" if region else "")
            is_best = ep is self._best
            row_color = "#00d2ff" if is_best else "#f0f0ff"
            star = "★ " if is_best else ""
            rows.append(
                f"<tr style='color:{row_color};'>"
                f"<td style='padding:3px 18px 3px 0;white-space:nowrap;'>{star}{name}</td>"
                f"<td align='right' style='padding:3px 12px;white-space:nowrap;'>{lat_cell(ep)}</td>"
                f"<td align='right' style='padding:3px 12px;white-space:nowrap;'>{up_cell(ep)}</td>"
                f"<td align='right' style='padding:3px 12px;white-space:nowrap;'>{tp_cell(ep)}</td>"
                f"<td align='right' style='padding:3px 0 3px 12px;white-space:nowrap;'>{ctx_cell(ep)}</td>"
                f"</tr>"
            )

        recommendation = ""
        if self._best is not None:
            recommendation = (
                f"<div style='margin-top:8px;color:#00d2ff;'>"
                f"★ Recommended: {self._best.provider_name} "
                f"<span style='color:#64648c;'>(fastest with ≥99% uptime)</span>"
                f"</div>"
            )

        model_name = self._display_model_name()
        return (
            f"<div style='font-size:10pt;font-weight:bold;'>{model_name}</div>"
            f"<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
            f"Live from openrouter.ai · refreshed every 5 min</div>"
            f"<table cellspacing='0' style='border-spacing:0;'>"
            f"<tr style='color:#64648c;font-size:8pt;'>"
            f"<th align='left' style='padding:3px 18px 6px 0;font-weight:600;'>PROVIDER</th>"
            f"<th align='right' style='padding:3px 12px 6px 12px;font-weight:600;'>LAT</th>"
            f"<th align='right' style='padding:3px 12px 6px 12px;font-weight:600;'>UPTIME</th>"
            f"<th align='right' style='padding:3px 12px 6px 12px;font-weight:600;'>SPEED</th>"
            f"<th align='right' style='padding:3px 0 6px 12px;font-weight:600;'>CTX</th>"
            f"</tr>"
            f"{''.join(rows)}"
            f"</table>"
            f"{recommendation}"
        )

    # ---- mouse handling for the info icon ----

    def mouseMoveEvent(self, event):
        pos = event.position()
        new_hover = self._icon_hit_rect.contains(pos)
        if new_hover != self._icon_hover:
            self._icon_hover = new_hover
            if new_hover:
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.unsetCursor()
            self.update()

    def leaveEvent(self, event):
        if self._icon_hover:
            self._icon_hover = False
            self.unsetCursor()
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._icon_hit_rect.contains(event.position()):
            center_local = self._icon_hit_rect.center()
            global_pos = self.mapToGlobal(center_local.toPoint())
            self.info_clicked.emit(self.model_id, QPointF(global_pos))

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        rect = QRectF(0, 0, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QBrush(Colors.BG_CARD))
        painter.setPen(QPen(Colors.BORDER, 1))
        painter.drawPath(path)

        # === Header layout: name (elided) · best chip · (i) icon ===
        # Reserve space right-to-left so the name never overlaps the chip
        # or the icon, no matter how long the name is.

        GAP_CHIP_TO_ICON = 10
        GAP_NAME_TO_CHIP = 12

        # Icon at the far right
        icon_right = w - self.PAD_X
        icon_x = icon_right - self.ICON_VISIBLE
        icon_y = self.PAD_Y + (self.HEADER_H - self.ICON_VISIBLE) / 2
        icon_pad = (self.ICON_HIT - self.ICON_VISIBLE) / 2
        self._icon_hit_rect = QRectF(
            icon_x - icon_pad, icon_y - icon_pad,
            self.ICON_HIT, self.ICON_HIT,
        )

        # Chip width via real font metrics
        chip_text = f"★ {self._best.provider_name}" if self._best is not None else ""
        chip_font = Fonts.tiny()
        chip_fm = QFontMetrics(chip_font)
        chip_w = chip_fm.horizontalAdvance(chip_text) if chip_text else 0

        # Chip placed left of icon
        chip_right = icon_x - GAP_CHIP_TO_ICON
        chip_left = chip_right - chip_w
        if not chip_text:
            chip_left = chip_right  # collapse to zero-width

        # Name fills from PAD_X up to chip_left - gap, with eliding
        name = self._display_model_name()
        name_fm = QFontMetrics(Fonts.subheading())
        name_max_w = max(40, int(chip_left - GAP_NAME_TO_CHIP - self.PAD_X))
        elided_name = name_fm.elidedText(name, Qt.TextElideMode.ElideRight, name_max_w)

        painter.setPen(Colors.TEXT_PRIMARY)
        painter.setFont(Fonts.subheading())
        painter.drawText(
            QRectF(self.PAD_X, self.PAD_Y, name_max_w, self.HEADER_H),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided_name,
        )

        # Chip (only if we have a best provider)
        if chip_text:
            painter.setPen(Colors.CYAN)
            painter.setFont(chip_font)
            painter.drawText(
                QRectF(chip_left, self.PAD_Y, chip_w, self.HEADER_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                chip_text,
            )

        # Info icon (cyan halo on hover)
        if self._icon_hover:
            painter.setBrush(QBrush(QColor(0, 210, 255, 38)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(self._icon_hit_rect)
            icon_pen_color = Colors.CYAN
        else:
            icon_pen_color = Colors.TEXT_MUTED

        painter.setPen(icon_pen_color)
        icon_font = Fonts.body()
        icon_font.setPointSize(11)
        painter.setFont(icon_font)
        painter.drawText(
            QRectF(icon_x, icon_y, self.ICON_VISIBLE, self.ICON_VISIBLE),
            Qt.AlignmentFlag.AlignCenter,
            "ⓘ",
        )

        y = self.PAD_Y + self.HEADER_H

        if self._error:
            painter.setPen(Colors.RED)
            painter.setFont(Fonts.body())
            painter.drawText(
                QRectF(0, y, w, self.ROW_H),
                Qt.AlignmentFlag.AlignCenter,
                "Failed to load",
            )
            painter.end()
            return

        if self._loading or not self._endpoints:
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(Fonts.body())
            painter.drawText(
                QRectF(0, y, w, self.ROW_H),
                Qt.AlignmentFlag.AlignCenter,
                "Loading…",
            )
            painter.end()
            return

        if not self._endpoints.endpoints:
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(Fonts.body())
            painter.drawText(
                QRectF(0, y, w, self.ROW_H),
                Qt.AlignmentFlag.AlignCenter,
                "No providers reported",
            )
            painter.end()
            return

        # Column geometry: right-align all three metric columns from the right edge
        # with explicit gaps so values never collide.
        PRICE_W = 82
        UPTIME_W = 36
        LATENCY_W = 46
        GAP = 10
        price_right = w - self.PAD_X
        up_right = price_right - PRICE_W - GAP
        lat_right = up_right - UPTIME_W - GAP
        name_x = self.PAD_X + 14
        name_max_w = lat_right - LATENCY_W - 8 - name_x

        for ep in self._endpoints.endpoints:
            is_best = self._best is ep

            if is_best:
                hi_path = QPainterPath()
                hi_path.addRoundedRect(
                    QRectF(self.PAD_X - 4, y + 2, w - 2 * (self.PAD_X - 4), self.ROW_H - 4),
                    6, 6,
                )
                hi = QColor(Colors.CYAN)
                hi.setAlpha(18)
                painter.fillPath(hi_path, QBrush(hi))

                painter.setPen(Colors.CYAN)
                painter.setFont(Fonts.body())
                painter.drawText(
                    QRectF(self.PAD_X, y, 12, self.ROW_H),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    "★",
                )

            painter.setPen(Colors.TEXT_PRIMARY if is_best else Colors.TEXT_SECONDARY)
            painter.setFont(Fonts.body())
            painter.drawText(
                QRectF(name_x, y, name_max_w, self.ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self._elide(self._provider_label(ep), name_max_w, Fonts.body()),
            )

            lat_text, lat_color = self._latency_chip(ep.latency_last_30m)
            painter.setPen(lat_color)
            painter.setFont(Fonts.mono_small())
            painter.drawText(
                QRectF(lat_right - LATENCY_W, y, LATENCY_W, self.ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                lat_text,
            )

            up_text, up_color = self._uptime_chip(ep.uptime)
            painter.setPen(up_color)
            painter.setFont(Fonts.mono_small())
            painter.drawText(
                QRectF(up_right - UPTIME_W, y, UPTIME_W, self.ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                up_text,
            )

            price_text = self._price(ep)
            painter.setPen(Colors.TEXT_ACCENT)
            painter.setFont(Fonts.mono_small())
            painter.drawText(
                QRectF(price_right - PRICE_W, y, PRICE_W, self.ROW_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                price_text,
            )

            y += self.ROW_H

        painter.end()

    # ---- helpers ----

    def _display_model_name(self):
        if self._endpoints and self._endpoints.model_name:
            n = self._endpoints.model_name
            if ": " in n:
                return n.split(": ", 1)[1]
            return n
        return self.model_id

    def _provider_label(self, ep):
        """Provider name with region suffix when the tag carries one,
        so two 'Amazon Bedrock' rows become 'Amazon Bedrock · us-east-1'
        and 'Amazon Bedrock · eu-west-1'."""
        region = ""
        if ep.tag and "/" in ep.tag:
            region = ep.tag.split("/", 1)[1]
        if region:
            return f"{ep.provider_name} · {region}"
        return ep.provider_name

    def _elide(self, text, max_w, font):
        fm = QFontMetrics(font)
        return fm.elidedText(text, Qt.TextElideMode.ElideRight, int(max_w))

    def _latency_chip(self, ms):
        if ms is None:
            return "—", Colors.TEXT_MUTED
        if ms < 1000:
            return f"{ms:.0f}ms", Colors.GREEN
        s = ms / 1000.0
        if s < 3:
            return f"{s:.1f}s", Colors.YELLOW
        return f"{s:.1f}s", Colors.ORANGE

    def _uptime_chip(self, pct):
        if pct is None:
            return "—", Colors.TEXT_MUTED
        if pct >= 99.0:
            return f"{pct:.0f}%", Colors.GREEN
        if pct >= 95.0:
            return f"{pct:.0f}%", Colors.YELLOW
        return f"{pct:.0f}%", Colors.RED

    def _price(self, ep):
        p, c = ep.price_per_mtok_prompt, ep.price_per_mtok_completion
        if p == 0 and c == 0:
            return "free"
        return f"{self._fmt_money(p)}/{self._fmt_money(c)}"

    def _fmt_money(self, v):
        if v == 0:
            return "$0"
        if v < 0.01:
            return f"${v:.3f}".rstrip("0").rstrip(".")
        if v < 1:
            return f"${v:.2f}"
        # Drop trailing .00 so $3.00 -> $3 (saves column width)
        if v == int(v):
            return f"${int(v)}"
        if v < 10:
            return f"${v:.2f}"
        return f"${v:.0f}"


# ---------------------------------------------------------------------------
#  Model picker (search + pin/unpin)
# ---------------------------------------------------------------------------
class ModelPickerRow(QWidget):
    """One row in the model picker dropdown. Click anywhere on the row
    to toggle its pin state."""

    toggled = Signal(str, bool)   # (model_id, is_pinned_after)

    ROW_H = 30

    def __init__(self, model_id, display_name, is_pinned, parent=None):
        super().__init__(parent)
        self.model_id = model_id
        self._display = display_name
        self._is_pinned = is_pinned
        self._hover = False
        self.setFixedHeight(self.ROW_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMouseTracking(True)

    def set_pinned(self, pinned):
        self._is_pinned = pinned
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pinned = not self._is_pinned
            self.toggled.emit(self.model_id, self._is_pinned)
            self.update()

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # Hover background
        if self._hover:
            path = QPainterPath()
            path.addRoundedRect(QRectF(2, 2, w - 4, h - 4), 6, 6)
            bg = QColor(Colors.CYAN)
            bg.setAlpha(18)
            painter.fillPath(path, QBrush(bg))

        # Star (filled if pinned, hollow if not)
        star_color = Colors.CYAN if self._is_pinned else Colors.TEXT_MUTED
        star_char = "★" if self._is_pinned else "☆"
        painter.setPen(star_color)
        f = Fonts.body()
        f.setPointSize(11)
        painter.setFont(f)
        painter.drawText(
            QRectF(12, 0, 20, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            star_char,
        )

        # Name (brighter when pinned)
        painter.setPen(Colors.TEXT_PRIMARY if self._is_pinned else Colors.TEXT_SECONDARY)
        painter.setFont(Fonts.body())
        fm = QFontMetrics(Fonts.body())
        text_x = 36
        text_w = w - text_x - 12
        painter.drawText(
            QRectF(text_x, 0, text_w, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            fm.elidedText(self._display, Qt.TextElideMode.ElideRight, int(text_w)),
        )

        painter.end()


class ModelPicker(QWidget):
    """Search bar + dropdown list of all OpenRouter models.

    Closed state: just the search bar.
    Open state: search bar + scrollable list of models (pinned at top
    with filled stars, others below with hollow stars). Click anywhere
    on a row to toggle pin/unpin.

    Open triggers: search bar gets focus, OR has any text.
    Close triggers: search bar loses focus AND text is empty.
    """

    pin_toggled = Signal(str, bool)  # (model_id, is_pinned_after)
    open_changed = Signal(bool)

    LIST_H = 260

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_models = []   # list of (id, display_name)
        self._pinned = set()
        self._is_open = False
        self._rows = []         # ModelPickerRow widgets for the current view

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search models to pin or unpin…")
        self.search.setClearButtonEnabled(True)  # built-in X to clear text
        self.search.textChanged.connect(self._on_text_changed)
        # Patch focus events to control the open/close lifecycle
        self.search.focusInEvent = self._wrap_focus_in
        self.search.focusOutEvent = self._wrap_focus_out
        layout.addWidget(self.search)

        # Dropdown is a sibling widget held outside the layout flow.
        # The dashboard reparents it to itself (via attach_overlay_to)
        # so it can be positioned with .move() and raised over the cards.
        # Top-level Tool windows hit weird interactions with the dashboard's
        # BypassWindowManagerHint and don't render reliably — overlay child
        # is the simpler, more robust pattern.
        self.list_card = QFrame()
        self.list_card.setObjectName("ModelPickerDropdown")
        self.list_card.setStyleSheet(
            "QFrame#ModelPickerDropdown { background: #1c1c32; "
            "border: 1px solid #323250; border-radius: 8px; }"
        )
        self.list_card.setFixedHeight(self.LIST_H)

        list_inner = QVBoxLayout(self.list_card)
        list_inner.setContentsMargins(0, 0, 0, 0)
        list_inner.setSpacing(0)

        self.scroll = QScrollArea(self.list_card)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent; border: none;")

        self.list_content = QWidget()
        self.list_content.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_content)
        self.list_layout.setContentsMargins(4, 4, 4, 4)
        self.list_layout.setSpacing(0)

        self.scroll.setWidget(self.list_content)
        list_inner.addWidget(self.scroll)
        self.list_card.hide()

        # App-wide event filter so any click outside the dropdown closes it
        self._dropdown_filter_installed = False

        # Debounce rebuilds while typing
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self._rebuild_list)

    # ---- public API ----

    def set_catalog(self, models):
        """models: iterable of objects with .id and .name attributes."""
        self._all_models = [(m.id, m.name) for m in models]
        if self._is_open:
            self._rebuild_list()

    def set_pinned(self, pinned_ids):
        self._pinned = set(pinned_ids)
        # Update star state on existing rows in place; rebuild only when open
        # changes the pinned section ordering on next open.
        for row in self._rows:
            row.set_pinned(row.model_id in self._pinned)

    def is_open(self):
        return self._is_open

    # ---- internal ----

    def _wrap_focus_in(self, event):
        QLineEdit.focusInEvent(self.search, event)
        self._open()

    def _wrap_focus_out(self, event):
        QLineEdit.focusOutEvent(self.search, event)
        # delay close to let row clicks register
        QTimer.singleShot(180, self._maybe_close)

    def _maybe_close(self):
        # Close on focus loss regardless of search text. Previously we
        # kept it open if there was text, but users expect a dropdown
        # to dismiss when they click away.
        if self.search.hasFocus():
            return
        self._close()

    def _on_text_changed(self, text):
        if text.strip():
            self._open()
        elif not self.search.hasFocus():
            self._close()
            return
        if self._is_open:
            self._rebuild_timer.start(120)

    def attach_overlay_to(self, overlay_parent):
        """Move list_card to be a child of `overlay_parent` so it can be
        absolutely positioned over arbitrary sibling widgets in the
        dashboard layout. Call this once from the dashboard during build."""
        self.list_card.setParent(overlay_parent)
        self.list_card.hide()

    def _open(self):
        if self._is_open:
            return
        self._is_open = True
        self._position_dropdown()
        self.list_card.show()
        self.list_card.raise_()
        self._rebuild_list()
        if not self._dropdown_filter_installed:
            QApplication.instance().installEventFilter(self)
            self._dropdown_filter_installed = True
        self.open_changed.emit(True)

    def _position_dropdown(self):
        """Place the dropdown under the search bar if there's room,
        otherwise above it. Width matches the search bar. The dropdown
        is clipped to the parent (dashboard) so we have to actually fit
        within those bounds.
        """
        parent = self.list_card.parent()
        if parent is None:
            return

        gap = 4
        search_w = self.search.width()
        search_h = self.search.height()
        search_top_g = self.search.mapToGlobal(QPoint(0, 0))
        search_bot_g = self.search.mapToGlobal(QPoint(0, search_h))
        parent_top_g = parent.mapToGlobal(QPoint(0, 0))
        parent_bot_g = parent.mapToGlobal(QPoint(0, parent.height()))

        space_below = parent_bot_g.y() - search_bot_g.y() - gap - 4
        space_above = search_top_g.y() - parent_top_g.y() - gap - 4

        if space_below >= self.LIST_H:
            target_y_g = search_bot_g.y() + gap
            h = self.LIST_H
        elif space_above >= self.LIST_H:
            target_y_g = search_top_g.y() - gap - self.LIST_H
            h = self.LIST_H
        elif space_above >= space_below:
            h = max(100, space_above)
            target_y_g = search_top_g.y() - gap - h
        else:
            h = max(100, space_below)
            target_y_g = search_bot_g.y() + gap

        local = parent.mapFromGlobal(QPoint(search_top_g.x(), target_y_g))
        self.list_card.move(local.x(), local.y())
        self.list_card.resize(search_w, h)

    def _close(self):
        if not self._is_open:
            return
        self._is_open = False
        self.list_card.hide()
        # Preserve the search text. Re-focusing the bar re-opens the list
        # with the same filter applied. Use the built-in X button on the
        # line edit to clear.
        self.open_changed.emit(False)

    def eventFilter(self, obj, event):
        """Close the dropdown on any mouse press outside the search bar
        AND outside the dropdown. Geometry comparisons must be in GLOBAL
        coords because list_card/search return their geometry in parent
        coords by default."""
        if (event.type() == QEvent.Type.MouseButtonPress
                and self._is_open):
            try:
                gp = event.globalPosition().toPoint()
            except AttributeError:
                gp = event.globalPos()
            from PySide6.QtCore import QRect
            dd_tl = self.list_card.mapToGlobal(QPoint(0, 0))
            dd_rect = QRect(dd_tl, self.list_card.size())
            s_tl = self.search.mapToGlobal(QPoint(0, 0))
            s_rect = QRect(s_tl, self.search.size())
            if not dd_rect.contains(gp) and not s_rect.contains(gp):
                self._close()
        return False

    def _rebuild_list(self):
        # Tear down
        while self.list_layout.count():
            child = self.list_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()
        self._rows = []

        query = self.search.text().strip().lower()
        pinned_rows = []
        unpinned_rows = []
        for mid, name in self._all_models:
            if query and query not in mid.lower() and query not in name.lower():
                continue
            display = name if name else mid
            row = ModelPickerRow(mid, display, mid in self._pinned, self.list_content)
            row.toggled.connect(self._on_row_toggled)
            self._rows.append(row)
            if mid in self._pinned:
                pinned_rows.append(row)
            else:
                unpinned_rows.append(row)

        # Pinned first, separator, then everything else
        for r in pinned_rows:
            self.list_layout.addWidget(r)
        if pinned_rows and unpinned_rows:
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet("background: #323250; margin: 4px 8px;")
            self.list_layout.addWidget(sep)
        for r in unpinned_rows:
            self.list_layout.addWidget(r)

        if not pinned_rows and not unpinned_rows:
            empty = QLabel("No matches" if query else "Loading catalog…")
            empty.setStyleSheet("color: #64648c; padding: 12px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setFont(Fonts.body())
            self.list_layout.addWidget(empty)

        self.list_layout.addStretch()

    def _on_row_toggled(self, model_id, is_pinned_after):
        if is_pinned_after:
            self._pinned.add(model_id)
        else:
            self._pinned.discard(model_id)
        self.pin_toggled.emit(model_id, is_pinned_after)
        # Restore focus to the search bar so the dropdown stays open and
        # the user can pin/unpin more in a single visit without the list
        # snapping shut on every click.
        self.search.setFocus(Qt.FocusReason.OtherFocusReason)


# ---------------------------------------------------------------------------
#  Pinned models column header (above the cards)
# ---------------------------------------------------------------------------
class PinnedColumnHeader(QWidget):
    """Tiny one-row label strip explaining what each card column means.

    Uses the SAME column geometry constants as PinnedModelCard so labels
    sit directly above the data they describe.
    """
    # mirror PinnedModelCard constants
    PAD_X = 14
    PRICE_W = 82
    UPTIME_W = 36
    LATENCY_W = 46
    GAP = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        price_right = w - self.PAD_X
        up_right = price_right - self.PRICE_W - self.GAP
        lat_right = up_right - self.UPTIME_W - self.GAP
        name_x = self.PAD_X + 14  # match the indent of card rows

        painter.setPen(Colors.TEXT_MUTED)
        painter.setFont(Fonts.label())

        painter.drawText(
            QRectF(name_x, 0, lat_right - self.LATENCY_W - 8 - name_x, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "PROVIDER",
        )
        # Short labels so they actually fit the narrow metric columns
        # at the same font weight as the section header.
        painter.drawText(
            QRectF(lat_right - self.LATENCY_W, 0, self.LATENCY_W, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            "LAT",
        )
        painter.drawText(
            QRectF(up_right - self.UPTIME_W, 0, self.UPTIME_W, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            "UP%",
        )
        painter.drawText(
            QRectF(price_right - self.PRICE_W, 0, self.PRICE_W, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            "$/M  IN·OUT",
        )
        painter.end()


# ---------------------------------------------------------------------------
#  Error Banner
# ---------------------------------------------------------------------------
class ErrorBanner(QWidget):
    """Dismissible banner shown at the top of the dashboard on API failure."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._message = ""
        self.setFixedHeight(0)  # collapsed by default
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_message(self, msg):
        self._message = msg
        if msg:
            self.setFixedHeight(28)
        else:
            self.setFixedHeight(0)
        self.update()

    def paintEvent(self, event):
        if not _safe_paint(self) or not self._message:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)

        bg = QColor(Colors.RED)
        bg.setAlpha(40)
        painter.fillPath(path, QBrush(bg))

        # Left edge accent stripe
        stripe = QRectF(0, 0, 3, self.height())
        painter.fillRect(stripe, QBrush(Colors.RED))

        painter.setPen(Colors.RED)
        painter.setFont(Fonts.label())
        painter.drawText(
            QRectF(12, 0, 60, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "API ERROR",
        )

        painter.setPen(Colors.TEXT_PRIMARY)
        painter.setFont(Fonts.body())
        fm = QFontMetrics(Fonts.body())
        elided = fm.elidedText(
            self._message,
            Qt.TextElideMode.ElideRight,
            int(self.width() - 84),
        )
        painter.drawText(
            QRectF(78, 0, self.width() - 84, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )
        painter.end()


# ---------------------------------------------------------------------------
#  Balance Timeline Chart
# ---------------------------------------------------------------------------
class TimelineChart(QWidget):
    """Real balance-over-time chart sourced from persisted History.

    Shows the last `window_seconds` of balance with a gradient fill,
    upward jumps marked as top-up dots, current value annotated at the
    right edge, and a baseline at the user's auto-top-up threshold (if
    configured).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series: list[tuple[float, float]] = []
        self._topups: list[tuple[float, float]] = []
        self._topup_threshold: float = 0.0
        self._window_label: str = ""
        self.setMinimumHeight(110)
        self.setMaximumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, series, topups, topup_threshold=0.0, window_label=""):
        self._series = list(series)
        self._topups = list(topups)
        self._topup_threshold = topup_threshold
        self._window_label = window_label
        self.update()

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        # Card background
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        painter.fillPath(bg, QBrush(Colors.BG_CARD))
        painter.setPen(QPen(Colors.BORDER, 1))
        painter.drawPath(bg)

        pad_x = 14
        pad_top = 22
        pad_bottom = 18
        chart_w = w - 2 * pad_x
        chart_h = h - pad_top - pad_bottom

        # Title
        painter.setPen(Colors.TEXT_SECONDARY)
        painter.setFont(Fonts.label())
        title = "BALANCE"
        if self._window_label:
            title += f" · {self._window_label.upper()}"
        painter.drawText(
            QRectF(pad_x, 4, chart_w, 18),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            title,
        )

        if len(self._series) < 2:
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(Fonts.body())
            painter.drawText(
                QRectF(0, 0, w, h),
                Qt.AlignmentFlag.AlignCenter,
                "Collecting data..." if not self._series else "Need more samples",
            )
            painter.end()
            return

        ts = [t for t, _ in self._series]
        vals = [v for _, v in self._series]
        t_min, t_max = ts[0], ts[-1]
        if t_max == t_min:
            t_max = t_min + 1
        v_max = max(vals)
        v_min = min(vals)
        # Pad the value range a bit so the line doesn't touch the top
        v_range = v_max - v_min
        if v_range < 0.01:
            v_range = max(v_max * 0.1, 1.0)
            v_max = v_max + v_range / 2
            v_min = max(0, v_min - v_range / 2)
            v_range = v_max - v_min

        def x_of(t):
            return pad_x + chart_w * (t - t_min) / (t_max - t_min)

        def y_of(v):
            return pad_top + chart_h * (1.0 - (v - v_min) / v_range)

        # Optional threshold baseline
        if self._topup_threshold > 0 and v_min <= self._topup_threshold <= v_max:
            y_th = y_of(self._topup_threshold)
            pen = QPen(Colors.TEXT_MUTED, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(int(pad_x), int(y_th), int(pad_x + chart_w), int(y_th))
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(Fonts.tiny())
            painter.drawText(
                QRectF(pad_x + chart_w - 80, y_th - 12, 80, 12),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"top-up @ ${self._topup_threshold:g}",
            )

        # Build the line path
        points = [QPointF(x_of(t), y_of(v)) for t, v in self._series]
        line_path = QPainterPath()
        line_path.moveTo(points[0])
        for p in points[1:]:
            line_path.lineTo(p)

        # Fill under the line with a gradient
        fill = QPainterPath(line_path)
        fill.lineTo(points[-1].x(), pad_top + chart_h)
        fill.lineTo(points[0].x(), pad_top + chart_h)
        fill.closeSubpath()
        grad = QLinearGradient(0, pad_top, 0, pad_top + chart_h)
        c_top = QColor(Colors.CYAN)
        c_top.setAlpha(80)
        c_bot = QColor(Colors.CYAN)
        c_bot.setAlpha(4)
        grad.setColorAt(0, c_top)
        grad.setColorAt(1, c_bot)
        painter.fillPath(fill, QBrush(grad))

        # Line on top
        painter.setPen(QPen(Colors.CYAN, 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(line_path)

        # Top-up markers
        ts_set = set(t for t, _ in self._topups)
        for t, v in self._series:
            if t in ts_set:
                px = x_of(t)
                py = y_of(v)
                glow = QColor(Colors.MAGENTA)
                glow.setAlpha(80)
                painter.setBrush(QBrush(glow))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(px, py), 6, 6)
                painter.setBrush(QBrush(Colors.MAGENTA))
                painter.drawEllipse(QPointF(px, py), 3, 3)

        # Current value bubble at the right
        last_p = points[-1]
        painter.setBrush(QBrush(Colors.CYAN))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(last_p, 3.5, 3.5)

        # Min/max labels on the right axis
        painter.setPen(Colors.TEXT_MUTED)
        painter.setFont(Fonts.tiny())
        painter.drawText(
            QRectF(pad_x, h - pad_bottom + 2, 80, 12),
            Qt.AlignmentFlag.AlignLeft,
            f"min ${v_min:.2f}",
        )
        painter.drawText(
            QRectF(pad_x + chart_w - 80, h - pad_bottom + 2, 80, 12),
            Qt.AlignmentFlag.AlignRight,
            f"now ${vals[-1]:.2f}",
        )
        painter.end()


def _format_price_pair(prompt, completion):
    """Format prompt/completion price per million tokens compactly.

    Shows `$X/M` if both are equal (or completion is zero), otherwise
    `$P / $C/M`.  Uses 2 decimals for >=$0.01 values, else strips
    trailing zeros to keep widths sane.
    """
    def fmt(x):
        if x == 0:
            return "0"
        if x < 0.01:
            s = f"{x:.4f}".rstrip("0").rstrip(".")
            return s if s else "0"
        return f"{x:.2f}"

    if completion == 0 or abs(prompt - completion) < 1e-9:
        return f"${fmt(prompt)}/M"
    return f"${fmt(prompt)} / ${fmt(completion)}/M"