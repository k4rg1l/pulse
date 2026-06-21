"""
OpenRouter Pulse - Custom Widgets
Hand-drawn gauges, sparklines, stat cards, status badges.
"""
import math
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QGraphicsDropShadowEffect, QSizePolicy, QLineEdit,
    QScrollArea, QFrame,
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, Property, QEasingCurve,
    QRectF, QPointF, Signal, QSize,
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
    """A styled section header with optional right-side text."""

    def __init__(self, title, right_text="", parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)

        left = QLabel(title.upper())
        left.setFont(Fonts.label())
        left.setStyleSheet("color: #a0a0c8;")
        layout.addWidget(left)

        layout.addStretch()

        self.right_label = QLabel(right_text)
        self.right_label.setFont(Fonts.tiny())
        self.right_label.setStyleSheet("color: #64648c;")
        layout.addWidget(self.right_label)


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