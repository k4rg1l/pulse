"""
OpenRouter Pulse - Custom Widgets
Hand-drawn gauges, sparklines, stat cards, status badges.
"""
import html
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
import theme_controller


# ---------------------------------------------------------------------------
#  Animated Arc Gauge
# ---------------------------------------------------------------------------
def _safe_paint(widget):
    """Return True if widget is safe to paint (has valid size)."""
    return widget.width() > 0 and widget.height() > 0


import re as _re
_HEX_COLOR = _re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _safe_color(value, fallback="#a0a0c8"):
    """Whitelist a hex color before it's interpolated into a QLabel CSS
    `style=` attribute. Trust-grade colors are hardcoded constants today, but
    this guards the rich-text render boundary against any future code path
    feeding an attacker-controlled color into an attribute (CSS injection)."""
    return value if isinstance(value, str) and _HEX_COLOR.match(value) else fallback


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

        # "credit" = severity color (balance danger stays visible);
        # "accent" = the active source accent (for usage/util rings).
        self._ring_mode = "credit"
        theme_controller.changed.connect(self.update)

    def set_ring_mode(self, mode):
        self._ring_mode = mode
        self.update()

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

        # Foreground arc (value): severity color for credit balance (danger
        # stays visible), or the active source accent for usage/util rings.
        if self._ring_mode == "accent":
            color = theme_controller.accent()
        else:
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

        # Center readout — amount / total / (optional) subtitle, stacked and
        # vertically centered on the arc center via font metrics, so the block
        # stays put whether or not the subtitle is present (no magic offsets).
        amount_f, total_f, sub_f = Fonts.mono_large(), Fonts.mono_small(), Fonts.tiny()
        amount_h = QFontMetrics(amount_f).height()
        total_h = QFontMetrics(total_f).height()
        sub_h = QFontMetrics(sub_f).height() if self._subtitle_text else 0
        top = cy - (amount_h + total_h + sub_h) / 2

        painter.setPen(Colors.TEXT_PRIMARY)
        painter.setFont(amount_f)
        painter.drawText(QRectF(0, top, w, amount_h),
                         Qt.AlignmentFlag.AlignCenter, self._amount_text)
        painter.setPen(Colors.TEXT_MUTED)
        painter.setFont(total_f)
        painter.drawText(QRectF(0, top + amount_h, w, total_h),
                         Qt.AlignmentFlag.AlignCenter, self._total_text)
        if self._subtitle_text:
            painter.setPen(Colors.TEXT_SECONDARY)
            painter.setFont(sub_f)
            painter.drawText(QRectF(0, top + amount_h + total_h, w, sub_h),
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
        # Flush with the card border below: no left margin, no inter-item
        # spacing. The chevron reserves zero width until set_collapsible()
        # turns it on, so non-collapsible headers (Usage, Burn Rate, etc.)
        # don't carry an empty chevron gap that pushes the title rightward.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.chevron = QLabel("")
        # Larger + bolder than the title text so the affordance reads
        # clearly. Letter-spaced label font garbles geometry glyphs, so
        # use a plain Segoe UI here.
        chev_font = QFont("Segoe UI", 11)
        chev_font.setWeight(QFont.Weight.Bold)
        self.chevron.setFont(chev_font)
        self.chevron.setStyleSheet("color: #a0a0c8;")
        # 0 until collapsible (see set_collapsible) so the title sits flush
        # with the card border on non-collapsible headers.
        self.chevron.setFixedWidth(0)
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
            # Reserve room for the glyph (16) + a small gap (4) before the
            # title. The chevron glyph itself renders flush at the card
            # border; the title follows it.
            self.chevron.setFixedWidth(20)
            self._refresh_chevron()
        else:
            self.unsetCursor()
            self.chevron.setText("")
            self.chevron.setFixedWidth(0)

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
        theme_controller.changed.connect(self.update)

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
        # No internal horizontal pad: the only inset is the burn card's 14px
        # layout margin, so the bar/text share the same left/right column as the
        # KPI cards and the timeline above/below it.
        pad = 0

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
        grad.setColorAt(0, theme_controller.accent())
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
        self.setFixedHeight(4)
        self._offset = 0.0
        self._ok = True
        theme_controller.changed.connect(self.update)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    def set_status(self, ok=True):
        self._ok = ok

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
        base = Colors.RED if not self._ok else theme_controller.accent()
        bright = base.lighter(160)
        o = self._offset
        # Solid accent across the whole top with a bright sheen sweeping over
        # it — fully covered (no dim gaps), just a moving highlight.
        grad.setColorAt(0.0, base)
        grad.setColorAt(o, bright)
        grad.setColorAt(1.0, base)

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
        self._accent = "#00d2ff"
        self._apply_frame_style()
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

    def _apply_frame_style(self):
        self._frame.setStyleSheet(
            "QFrame#ProviderPopupFrame {"
            "  background: #1c1c32;"
            f"  border: 1px solid {self._accent};"
            "  border-radius: 10px;"
            "}"
            "QLabel { color: #f0f0ff; font-family: 'Segoe UI'; font-size: 9pt; }"
        )

    def set_accent(self, hex_color: str):
        """Recolor the popup border (e.g. to a model's Arena tier color)."""
        if hex_color and hex_color != self._accent:
            self._accent = hex_color
            self._apply_frame_style()

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
    CREST_H = 28        # the Arena rank-crest band pill (only when benchmark data)
    SPEED_H = 28        # the Speed Percentile band pill (only when speed data)
    # Uniform vertical rhythm. The header content already sits ~BAND_GAP above the
    # first band (its centered slack), bands are BAND_GAP apart, and ROWS_GAP — a
    # touch smaller, to offset the first provider row's own top slack — sits below
    # the last band. Net: visually equal gaps header↔band↔band↔rows.
    BAND_GAP = 6
    ROWS_GAP = 3
    CHEV_GAP = 7        # gap from a band's trailing value to its right chevron
    PAD_X = 14
    PAD_Y = 8
    ICON_VISIBLE = 16   # rendered glyph
    ICON_HIT = 22       # hit area (slightly bigger for usability)
    SEAL_W = 14         # the per-provider Trust Seal slot (The Ledger)

    info_clicked = Signal(str, QPointF)    # (model_id, global anchor pos)
    arena_clicked = Signal(str, QPointF)   # crest band clicked -> Fighter Card
    trust_clicked = Signal(str, str, QPointF)  # (model_id, provider_ident, anchor)
    speed_clicked = Signal(str, QPointF)   # speed band clicked -> Time Slip dossier

    def __init__(self, model_id, parent=None):
        super().__init__(parent)
        self.model_id = model_id
        self._endpoints = None       # ModelEndpoints or None
        self._error = False
        self._loading = True
        self._best = None
        self._icon_hit_rect = QRectF()  # set in paintEvent
        self._icon_hover = False
        # Arena standings (BenchmarkEntry or None)
        self._benchmark = None
        self._crest_hit_rect = QRectF()
        self._crest_hover = False
        # The Ledger — per-provider privacy/trust seals
        self._provider_trust = None      # ProviderTrustBook or None
        self._seal_hits = []             # [(QRectF, ident, accent_hex)] per row
        self._seal_hover_ident = None
        self._logo_store = None          # shared LogoStore (#2b), or None
        # Speed Percentile (#4) — the fleet-relative velocity band ("Time Slip")
        self._speed = None               # SpeedStanding or None
        self._speed_hit_rect = QRectF()
        self._speed_hover = False
        self._speed_elite = False        # top-decile throughput → heat-haze shimmer
        # Render introspection (set during paint) — measured by deterministic tests
        self._speed_lane_rect = QRectF()
        self._speed_marker_x = 0.0       # comet head x (throughput percentile)
        self._speed_reaction_x = None    # latency reaction-tick x, or None
        self._crest_emblem_cx = 0.0      # Arena hexagon center x
        self._crest_content_x = 0.0      # Arena text column x
        self._speed_emblem_cx = 0.0      # speed bolt center x
        # Shimmer phase is shared by the Arena crest sweep AND the elite speed
        # comet; the one timer runs whenever EITHER band wants it.
        self._shimmer = 0.0
        self._arena_elite = False
        self._shimmer_timer = QTimer(self)
        self._shimmer_timer.setInterval(55)
        self._shimmer_timer.timeout.connect(self._advance_shimmer)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._update_height()

    def _update_height(self):
        rows = len(self._endpoints.endpoints) if self._endpoints else 1
        crest = self.CREST_H if self._benchmark is not None else 0
        speed = self.SPEED_H if self._speed is not None else 0
        has_band = self._benchmark is not None or self._speed is not None
        both = self._benchmark is not None and self._speed is not None
        inter = self.BAND_GAP if both else 0       # gap between the two bands
        below = self.ROWS_GAP if has_band else 0    # gap before the provider rows
        h = (self.HEADER_H + crest + inter + speed + below
             + max(1, rows) * self.ROW_H + self.PAD_Y * 2)
        self.setFixedHeight(h)

    # ---- Shimmer (shared by the Arena crest + the elite speed comet) ----

    def _wants_shimmer(self) -> bool:
        return self._arena_elite or self._speed_elite

    def _sync_shimmer(self):
        """Run the one shared shimmer timer iff some band wants it AND we're
        visible; stop it otherwise. Cheap no-op when state is unchanged."""
        if self._wants_shimmer() and self.isVisible():
            if not self._shimmer_timer.isActive():
                self._shimmer_timer.start()
        elif not self._wants_shimmer():
            self._shimmer_timer.stop()

    # ---- Arena (benchmark standings) ----

    def set_benchmark(self, entry):
        """entry: BenchmarkEntry or None. Adds/removes the crest band."""
        had = self._benchmark is not None
        self._benchmark = entry
        self._arena_elite = bool(entry is not None and entry.is_elite)
        self._sync_shimmer()
        if (entry is not None) != had:
            self._update_height()
        self.update()

    def has_benchmark(self) -> bool:
        return self._benchmark is not None

    # ---- Speed Percentile (#4 — the "Time Slip" velocity band) ----

    def set_speed(self, standing):
        """standing: a frontend_client.SpeedStanding or None. None removes the
        velocity band (model not in the ranked field). The dashboard preserves
        the last-good *board* on a failed refresh, so a transient fetch error
        doesn't blank the band."""
        had = self._speed is not None
        self._speed = standing
        self._speed_elite = bool(standing is not None and standing.is_elite)
        self._sync_shimmer()
        if (standing is not None) != had:
            self._update_height()
        self.update()

    def has_speed(self) -> bool:
        return self._speed is not None

    def speed_accent(self) -> str:
        return _safe_color(self._speed.tier[1], "#00d2ff") if self._speed else "#00d2ff"

    # ---- Shared left rail ----
    # The crest + speed band emblems sit in the SAME column as each provider
    # row's trust seal, and the band text starts at the SAME x as the provider
    # names — so emblems/seals and band-text/names line up vertically down the
    # whole card. One source of truth for all three.
    def _icon_col_cx(self) -> float:
        return self.PAD_X + self.SEAL_W / 2.0      # seal / band-emblem center x

    def _content_col_x(self) -> float:
        return self.PAD_X + self.SEAL_W + 3         # provider-name / band-text left x

    # ---- The Ledger (per-provider trust seals) ----

    def set_provider_trust(self, book):
        """book: a ProviderTrustBook or None. Seals appear on each provider
        row once a matching provider is found."""
        self._provider_trust = book
        self.update()

    def _trust_for_ep(self, ep):
        """(ProviderTrust, CustodyGrade) for an endpoint, or (None, None)."""
        if self._provider_trust is None:
            return None, None
        p = self._provider_trust.lookup(name=ep.provider_name, tag=ep.tag)
        if p is None:
            return None, None
        from frontend_client import custody_score
        return p, custody_score(p)

    def _ep_ident(self, ep):
        """A stable per-row identity for the trust click target."""
        return ep.tag or ep.provider_name

    # ---- Provider logos (#2b) ----

    def set_logo_store(self, store):
        self._logo_store = store

    def _provider_for_ident(self, ident):
        ep = self._ep_by_ident(ident)
        if ep is None:
            return None
        p, _g = self._trust_for_ep(ep)
        return p

    def provider_slug_for(self, ident):
        p = self._provider_for_ident(ident)
        return p.slug if p is not None else None

    def request_logo(self, ident):
        """Ask the shared cache to fetch this provider's logo (idempotent)."""
        if self._logo_store is None:
            return
        p = self._provider_for_ident(ident)
        if p is not None and p.slug and p.icon_abs_url:
            self._logo_store.request(p.slug, p.icon_abs_url)

    def logos_needed(self):
        """(slug, url) for every provider on this card that has trust data +
        a logo — used by the dashboard to pre-warm the cache."""
        out = []
        if self._provider_trust is None or not self._endpoints:
            return out
        for ep in self._endpoints.endpoints:
            p, _g = self._trust_for_ep(ep)
            if p is not None and p.slug and p.icon_abs_url:
                out.append((p.slug, p.icon_abs_url))
        return out

    def arena_accent(self) -> str:
        return self._benchmark.tier[1] if self._benchmark else "#00d2ff"

    def display_name(self) -> str:
        return self._display_model_name()

    def showEvent(self, event):
        self._sync_shimmer()
        super().showEvent(event)

    def hideEvent(self, event):
        self._shimmer_timer.stop()
        super().hideEvent(event)

    def _advance_shimmer(self):
        self._shimmer = (self._shimmer + 0.045) % 1.0
        self.update()

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

        def trust_cell(ep):
            _, g = self._trust_for_ep(ep)
            if g is None:
                return "<span style='color:#3a3a52;'>—</span>"
            return (f"<span style='color:{_safe_color(g.color)};font-weight:bold;'>"
                    f"{html.escape(g.grade)}</span>"
                    f"<span style='color:#5a5a78;'> {int(g.score)}</span>")

        # Use nowrap on provider name so long region tags
        # (e.g. "Amazon Bedrock · eu-west-1") don't wrap and break row alignment.
        # Tight cell padding keeps rows visually compact.
        rows = []
        for ep in self._endpoints.endpoints:
            region = ""
            if ep.tag and "/" in ep.tag:
                region = ep.tag.split("/", 1)[1]
            name = html.escape(ep.provider_name) + (
                f" · {html.escape(region)}" if region else "")
            is_best = ep is self._best
            row_color = "#00d2ff" if is_best else "#f0f0ff"
            star = "★ " if is_best else ""
            rows.append(
                f"<tr style='color:{row_color};'>"
                f"<td style='padding:3px 18px 3px 0;white-space:nowrap;'>{star}{name}</td>"
                f"<td align='center' style='padding:3px 12px;white-space:nowrap;'>{trust_cell(ep)}</td>"
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

        model_name = html.escape(self._display_model_name())
        return (
            f"<div style='font-size:10pt;font-weight:bold;'>{model_name}</div>"
            f"<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
            f"Live from openrouter.ai · refreshed every 5 min</div>"
            f"<table cellspacing='0' style='border-spacing:0;'>"
            f"<tr style='color:#64648c;font-size:8pt;'>"
            f"<th align='left' style='padding:3px 18px 6px 0;font-weight:600;'>PROVIDER</th>"
            f"<th align='center' style='padding:3px 12px 6px 12px;font-weight:600;'>TRUST</th>"
            f"<th align='right' style='padding:3px 12px 6px 12px;font-weight:600;'>LAT</th>"
            f"<th align='right' style='padding:3px 12px 6px 12px;font-weight:600;'>UPTIME</th>"
            f"<th align='right' style='padding:3px 12px 6px 12px;font-weight:600;'>SPEED</th>"
            f"<th align='right' style='padding:3px 0 6px 12px;font-weight:600;'>CTX</th>"
            f"</tr>"
            f"{''.join(rows)}"
            f"</table>"
            f"{recommendation}"
        )

    # ---- The Dossier (per-provider trust deep-dive popup) ----

    def _ep_by_ident(self, ident):
        if not self._endpoints:
            return None
        for ep in self._endpoints.endpoints:
            if self._ep_ident(ep) == ident:
                return ep
        return None

    def dossier_accent(self, ident) -> str:
        ep = self._ep_by_ident(ident)
        if ep is not None:
            _, g = self._trust_for_ep(ep)
            if g is not None:
                return _safe_color(g.color, "#00d2ff")
        return "#00d2ff"

    def dossier_html(self, ident) -> str:
        """The Custody dossier for one provider: the verdict, an auditable rap
        sheet that sums to the Custody Score, and the jurisdiction trail."""
        ep = self._ep_by_ident(ident)
        if ep is None:
            return ""
        p, g = self._trust_for_ep(ep)
        if p is None or g is None:
            return ""

        name = html.escape(p.name or ep.provider_name or "Provider")
        mono = html.escape((p.name or "?")[:1].upper())
        gcol = _safe_color(g.color)
        grade = html.escape(g.grade)
        score = int(g.score)
        # Header: real logo tile if cached, else a grade-colored monogram chip.
        logo_html = None
        if self._logo_store is not None and p.slug:
            logo_html = self._logo_store.tile_html(p.slug, px=40)
        avatar = logo_html or (
            f"<span style='background-color:{gcol};color:#10101c;font-weight:bold;"
            f"font-size:11pt;padding:2px 7px;border-radius:6px;'>{mono}</span>")
        head = (
            f"<table cellspacing='0'><tr>"
            f"<td style='padding:0 8px 0 0;'>{avatar}</td>"
            f"<td style='padding:0;'>"
            f"<span style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</span><br>"
            f"<span style='font-size:8pt;color:#64648c;'>Custody dossier · openrouter.ai</span>"
            f"</td>"
            f"<td align='right' style='padding:0 0 0 18px;'>"
            f"<span style='font-size:15pt;font-weight:bold;color:{gcol};'>{grade}</span>"
            f"<span style='font-size:9pt;color:#a0a0c8;'> &nbsp;{score}<span style='color:#5a5a78;'>/100</span></span>"
            f"</td></tr></table>"
        )

        verdict = (
            f"<div style='margin:6px 0 8px 0;color:#c8c8e0;font-size:9pt;'>"
            f"{html.escape(self._trust_verdict(p))}</div>"
        )

        # Rap sheet: offenses (red, with deductions) + clean checks (green),
        # then the sum so the score is auditable.
        rows = []
        for pen in g.penalties:
            # Active harms strike in bright red; lesser deductions in amber.
            col = "#f08a8a" if pen.offense else "#d9a96a"
            rows.append(
                f"<tr><td style='padding:1px 14px 1px 0;color:{col};white-space:nowrap;'>"
                f"{html.escape(pen.label)}</td>"
                f"<td align='right' style='padding:1px 0;color:{col};white-space:nowrap;'>{pen.delta}</td></tr>")
        for pos in g.positives:
            rows.append(
                f"<tr><td style='padding:1px 14px 1px 0;color:#7fd99a;white-space:nowrap;'>"
                f"&#10003; {html.escape(pos)}</td>"
                f"<td align='right' style='padding:1px 0;color:#5a7a64;'>&nbsp;</td></tr>")
        rap = (
            f"<table cellspacing='0' style='font-size:8.5pt;border-spacing:0;'>"
            f"{''.join(rows)}"
            f"<tr><td colspan='2' style='border-top:1px solid #323250;padding-top:3px;'></td></tr>"
            f"<tr><td style='padding:1px 14px 0 0;color:#a0a0c8;font-weight:bold;'>= Custody Score</td>"
            f"<td align='right' style='padding:1px 0 0 0;color:{gcol};font-weight:bold;'>"
            f"{score}/100 · {grade}</td></tr>"
            f"</table>"
        )

        return f"{head}{verdict}{rap}{self._jurisdiction_html(p)}{self._dossier_footer(p)}"

    def _trust_verdict(self, p) -> str:
        hq = p.headquarters or "Unknown-HQ"
        train = "trains on your prompts" if p.trains else "never trains"
        if not p.retains:
            ret = "zero retention"
        elif p.retention_days:
            ret = f"deletes prompts after {p.retention_days} days"
        else:
            ret = "retains prompts (term undisclosed)"
        parts = [f"{hq}-based", train, ret]
        if p.can_publish:
            parts.append("may publish prompts")
        return " · ".join(parts)

    def _jurisdiction_html(self, p) -> str:
        def chip(code, color="#2a2a44", fg="#c8c8e0"):
            return (f"<span style='background-color:{color};color:{fg};font-size:8pt;"
                    f"font-weight:bold;padding:1px 6px;border-radius:4px;'>"
                    f"{html.escape(code)}</span>")
        hq = p.headquarters or "??"
        bits = [f"<span style='color:#64648c;font-size:8pt;'>HQ</span> {chip(hq)}"]
        if p.datacenters:
            cross = any(c != p.headquarters for c in p.datacenters)
            dc_chips = " ".join(
                chip(c, "#4a3a1a" if c != p.headquarters else "#2a2a44",
                     "#ffd277" if c != p.headquarters else "#c8c8e0")
                for c in p.datacenters)
            bits.append(f"<span style='color:#64648c;font-size:8pt;'>&nbsp;&nbsp;servers</span> {dc_chips}")
            if cross:
                bits.append("<span style='color:#ffd277;font-size:8pt;'>&nbsp;· prompt leaves home jurisdiction</span>")
        elif not p.datacenters_known:
            bits.append("<span style='color:#7a7a96;font-size:8pt;'>&nbsp;&nbsp;servers undisclosed</span>")
        return f"<div style='margin-top:8px;'>{''.join(bits)}</div>"

    def _dossier_footer(self, p) -> str:
        notes = []
        if not p.retains:
            notes.append("prompts never stored")
        elif p.retention_days:
            notes.append(f"prompt deleted in {p.retention_days} days")
        if p.byok_enabled:
            notes.append("BYOK available")
        if not notes:
            return ""
        return (f"<div style='margin-top:6px;color:#64648c;font-size:8pt;'>"
                f"{html.escape(' · '.join(notes))}</div>")

    # ---- mouse handling for the info icon ----

    def _seal_at(self, pos):
        """(rect, ident) of the Trust Seal under `pos`, or (None, None)."""
        for rect, ident, _color in self._seal_hits:
            if rect.contains(pos):
                return rect, ident
        return None, None

    def mouseMoveEvent(self, event):
        pos = event.position()
        icon_hover = self._icon_hit_rect.contains(pos)
        crest_hover = self._benchmark is not None and self._crest_hit_rect.contains(pos)
        speed_hover = self._speed is not None and self._speed_hit_rect.contains(pos)
        _, seal_ident = self._seal_at(pos)
        changed = False
        if icon_hover != self._icon_hover:
            self._icon_hover = icon_hover
            changed = True
        if crest_hover != self._crest_hover:
            self._crest_hover = crest_hover
            changed = True
        if speed_hover != self._speed_hover:
            self._speed_hover = speed_hover
            changed = True
        if seal_ident != self._seal_hover_ident:
            self._seal_hover_ident = seal_ident
            changed = True
        if changed:
            if icon_hover or crest_hover or speed_hover or seal_ident is not None:
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.unsetCursor()
            self.update()

    def leaveEvent(self, event):
        if (self._icon_hover or self._crest_hover or self._speed_hover
                or self._seal_hover_ident is not None):
            self._icon_hover = False
            self._crest_hover = False
            self._speed_hover = False
            self._seal_hover_ident = None
            self.unsetCursor()
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        seal_rect, seal_ident = self._seal_at(pos)
        if self._icon_hit_rect.contains(pos):
            global_pos = self.mapToGlobal(self._icon_hit_rect.center().toPoint())
            self.info_clicked.emit(self.model_id, QPointF(global_pos))
        elif self._benchmark is not None and self._crest_hit_rect.contains(pos):
            global_pos = self.mapToGlobal(self._crest_hit_rect.center().toPoint())
            self.arena_clicked.emit(self.model_id, QPointF(global_pos))
        elif self._speed is not None and self._speed_hit_rect.contains(pos):
            global_pos = self.mapToGlobal(self._speed_hit_rect.center().toPoint())
            self.speed_clicked.emit(self.model_id, QPointF(global_pos))
        elif seal_ident is not None:
            global_pos = self.mapToGlobal(seal_rect.center().toPoint())
            self.trust_clicked.emit(self.model_id, seal_ident, QPointF(global_pos))

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

        # Shared baseline for the name and the ★ chip so the two different-size
        # fonts sit on ONE line (AlignVCenter would center each line-box and
        # leave the smaller chip riding ~2px high). Baseline is centered on the
        # header band using the dominant (name) font's metrics.
        baseline = self.PAD_Y + (self.HEADER_H + name_fm.ascent() - name_fm.descent()) / 2.0

        painter.setPen(Colors.TEXT_PRIMARY)
        painter.setFont(Fonts.subheading())
        painter.drawText(QPointF(self.PAD_X, baseline), elided_name)

        # Chip (only if we have a best provider) — same baseline as the name
        if chip_text:
            painter.setPen(Colors.CYAN)
            painter.setFont(chip_font)
            painter.drawText(QPointF(float(chip_left), baseline), chip_text)

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

        # Bands stack below the header with a uniform vertical rhythm. The header
        # content already sits ~BAND_GAP above the first band (its centered slack),
        # so we add NO gap there; the two bands are BAND_GAP apart; ROWS_GAP sits
        # before the provider rows. Net: visually equal gaps down the card.
        y = self.PAD_Y + self.HEADER_H
        drew_band = False

        if self._benchmark is not None:
            self._paint_crest(painter, y)
            y += self.CREST_H
            drew_band = True
        else:
            self._crest_hit_rect = QRectF()

        if self._speed is not None:
            if drew_band:
                y += self.BAND_GAP
            self._paint_speed(painter, y)
            y += self.SPEED_H
            drew_band = True
        else:
            self._speed_hit_rect = QRectF()

        if drew_band:
            y += self.ROWS_GAP

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
        # Leave the left slot for the per-provider Trust Seal (or the ★ marker
        # when no trust data is available).
        name_x = self._content_col_x()
        name_max_w = lat_right - LATENCY_W - 8 - name_x

        self._seal_hits = []
        for ep in self._endpoints.endpoints:
            is_best = self._best is ep
            p_trust, grade = self._trust_for_ep(ep)

            if is_best:
                hi_path = QPainterPath()
                hi_path.addRoundedRect(
                    QRectF(self.PAD_X - 4, y + 2, w - 2 * (self.PAD_X - 4), self.ROW_H - 4),
                    6, 6,
                )
                hi = QColor(Colors.CYAN)
                hi.setAlpha(18)
                painter.fillPath(hi_path, QBrush(hi))
                # A 2px gold accent at the row's left edge marks the best
                # provider without fighting the seal for the left slot.
                painter.fillRect(QRectF(self.PAD_X - 4, y + 3, 2, self.ROW_H - 6),
                                 QBrush(QColor("#ffd23f")))

            if grade is not None:
                seal_box = QRectF(self.PAD_X, y + (self.ROW_H - 16) / 2, self.SEAL_W, 16)
                hover = (self._seal_hover_ident == self._ep_ident(ep))
                self._paint_trust_seal(painter, seal_box, grade, hover)
                self._seal_hits.append(
                    (QRectF(self.PAD_X - 2, y, self.SEAL_W + 4, self.ROW_H),
                     self._ep_ident(ep), grade.color))
            elif is_best:
                # No trust data — fall back to the classic ★ best marker,
                # centered in the SAME seal column (cx = PAD_X + SEAL_W/2) so the
                # marker doesn't shift when a row has a shield vs the ★ fallback.
                painter.setPen(Colors.CYAN)
                painter.setFont(Fonts.body())
                painter.drawText(
                    QRectF(self.PAD_X, y, self.SEAL_W, self.ROW_H),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
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

    # ---- Arena crest rendering ----

    def _paint_crest(self, painter, y):
        e = self._benchmark
        tier_name, tier_hex = e.tier
        tier = QColor(tier_hex)
        band = QRectF(self.PAD_X - 2, y,
                      self.width() - 2 * (self.PAD_X - 2), self.CREST_H)
        self._crest_hit_rect = band

        painter.save()
        bg = QColor(tier)
        bg.setAlpha(46 if self._crest_hover else 26)
        bpath = QPainterPath()
        bpath.addRoundedRect(band, 8, 8)
        painter.fillPath(bpath, QBrush(bg))
        bd = QColor(tier)
        bd.setAlpha(110 if self._crest_hover else 55)
        painter.setPen(QPen(bd, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(bpath)

        # Emblem sits in the SAME slot as each provider row's trust seal, and the
        # text starts at the provider-name column — so the band lines up with the
        # rows below it (see _icon_col_cx / _content_col_x).
        EMB = float(self.SEAL_W)
        icon_cx = self._icon_col_cx()
        erect = QRectF(icon_cx - EMB / 2, band.center().y() - EMB / 2, EMB, EMB)
        self._crest_emblem_cx = icon_cx
        self._paint_emblem(painter, erect, tier, e.is_elite)

        # TIER  ·  #rank CATEGORY  ............  ELO  ›
        tx = self._content_col_x()
        self._crest_content_x = tx
        tf = Fonts.tiny()
        tf.setBold(True)
        painter.setFont(tf)
        painter.setPen(tier)
        tfm = QFontMetrics(tf)
        painter.drawText(QRectF(tx, band.top(), 160, band.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         tier_name)
        cur = tx + tfm.horizontalAdvance(tier_name) + 9

        sig = e.signature
        if sig:
            sf = Fonts.tiny()
            painter.setFont(sf)
            sfm = QFontMetrics(sf)
            painter.setPen(QColor(96, 96, 130))
            painter.drawText(QRectF(cur - 7, band.top(), 8, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "·")
            rank_txt = f"#{sig.rank}"
            painter.setPen(QColor(222, 222, 244))
            painter.drawText(QRectF(cur, band.top(), 44, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, rank_txt)
            cur += sfm.horizontalAdvance(rank_txt) + 5
            painter.setPen(QColor(124, 124, 156))
            painter.drawText(QRectF(cur, band.top(), 140, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             sig.category.upper())

        # right edge: ELO + clickable chevron
        chev_w = 12
        chev_x = band.right() - 8 - chev_w
        painter.setFont(Fonts.body())
        painter.setPen(tier if self._crest_hover else QColor(120, 120, 150))
        painter.drawText(QRectF(chev_x, band.top() - 1, chev_w, band.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter, "›")
        if e.peak_elo:
            ef = Fonts.mono_small()
            painter.setFont(ef)
            efm = QFontMetrics(ef)
            elo_txt = str(e.peak_elo)
            elo_w = efm.horizontalAdvance(elo_txt) + 2
            painter.setPen(tier)
            painter.drawText(QRectF(chev_x - self.CHEV_GAP - elo_w, band.top(), elo_w, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, elo_txt)
        painter.restore()

    # ---- Speed Percentile rendering (the "Time Slip" drag-strip band) ----

    def _paint_speed(self, painter, y):
        """A clean fleet-speed meter: a lightning-bolt emblem, then a dim rounded
        track with a cyan gradient fill whose LENGTH is the throughput percentile
        (vs the whole ranked field), capped by a glowing knob. The tier word +
        'faster than NN%' read out the value; a chevron opens the full two-axis
        Speed dossier. Fixed-px / font-metric-driven so it never clips; dominant
        color is brand cyan so it never fights the Arena tiers above it."""
        st = self._speed
        band = QRectF(self.PAD_X - 2, y,
                      self.width() - 2 * (self.PAD_X - 2), self.SPEED_H)
        self._speed_hit_rect = band
        cy = band.center().y()
        ACCENT = QColor(0, 210, 255)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Band backing (same idiom as the crest band, but brand cyan).
        bg = QColor(ACCENT); bg.setAlpha(34 if self._speed_hover else 18)
        bpath = QPainterPath(); bpath.addRoundedRect(band, 8, 8)
        painter.fillPath(bpath, QBrush(bg))
        bd = QColor(ACCENT); bd.setAlpha(105 if self._speed_hover else 45)
        painter.setPen(QPen(bd, 1)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(bpath)

        tp = st.throughput_pct if st.throughput_pct is not None else 0.0

        # --- Right text zone: [TIER] · faster than NN%  + chevron ---
        tier_name, tier_hex = st.tier
        tier_col = QColor(_safe_color(tier_hex, "#00d2ff"))
        f = Fonts.tiny(); f.setBold(True)
        fm = QFontMetrics(f)
        pct = round(tp * 100)
        head_full = f"faster than {pct}%"
        sep = " · "
        chev_w = 12
        chev_x = band.right() - 8 - chev_w

        def text_total(show_tier):
            tw = (fm.horizontalAdvance(tier_name) + fm.horizontalAdvance(sep)) if show_tier else 0
            return tw + fm.horizontalAdvance(head_full)

        # Lane starts on the SAME content column as the crest text + provider names.
        startX = self._content_col_x()
        MIN_LANE = 44
        show_tier = True
        text_left = chev_x - self.CHEV_GAP - text_total(show_tier)
        if text_left - 8 - startX < MIN_LANE:          # not enough lane → drop tier word
            show_tier = False
            text_left = chev_x - self.CHEV_GAP - text_total(show_tier)
        head_text, elided = head_full, False
        if text_left - 8 - startX < MIN_LANE:          # still tight → elide headline
            lane_end = startX + MIN_LANE
            avail = max(0, int(chev_x - self.CHEV_GAP - (lane_end + 8)))
            head_text = fm.elidedText(head_full, Qt.TextElideMode.ElideRight, avail)
            text_left = chev_x - self.CHEV_GAP - fm.horizontalAdvance(head_text)
            elided = True
        lane_end = max(startX + 1, text_left - 8)
        lane_len = lane_end - startX
        self._speed_lane_rect = QRectF(startX, cy - 3, lane_len, 6)

        # --- speed emblem: a clean lightning bolt in the left slot. Centered on
        #     the SAME column as the crest's hexagon emblem (band.left()+18) so the
        #     two bands' emblems line up vertically. ---
        self._speed_emblem_cx = self._icon_col_cx()
        self._paint_speed_bolt(painter, self._speed_emblem_cx, cy, float(self.SEAL_W), ACCENT)

        # --- the meter: dim rounded track + cyan gradient fill whose length is
        #     the throughput percentile, capped by a glowing knob ---
        self._speed_reaction_x = None          # latency lives in the dossier, not the band
        TRACK_H = 5.0
        painter.setPen(Qt.PenStyle.NoPen)
        track = QPainterPath()
        track.addRoundedRect(QRectF(startX, cy - TRACK_H / 2, lane_len, TRACK_H),
                             TRACK_H / 2, TRACK_H / 2)
        painter.fillPath(track, QBrush(QColor(40, 40, 62)))

        fill_end = startX + tp * lane_len
        self._speed_marker_x = fill_end
        if fill_end > startX + 0.5:
            fillpath = QPainterPath()
            fillpath.addRoundedRect(QRectF(startX, cy - TRACK_H / 2,
                                           fill_end - startX, TRACK_H),
                                    TRACK_H / 2, TRACK_H / 2)
            grad = QLinearGradient(startX, cy, fill_end, cy)
            grad.setColorAt(0.0, QColor(0, 146, 196))
            grad.setColorAt(1.0, QColor(124, 236, 255))
            painter.fillPath(fillpath, QBrush(grad))

        # glowing knob at the fill head (the throughput read)
        painter.setBrush(QBrush(QColor(0, 210, 255, 70)))
        painter.drawEllipse(QPointF(fill_end, cy), 6.5, 6.5)
        painter.setBrush(QBrush(Colors.BG_CARD))
        painter.drawEllipse(QPointF(fill_end, cy), 4.6, 4.6)
        painter.setBrush(QBrush(QColor(214, 248, 255)))
        painter.drawEllipse(QPointF(fill_end, cy), 3.4, 3.4)
        if self._speed_elite:
            # WARP tier: a tiny heat-haze sparkle riding the knob
            off = math.sin(self._shimmer * 2 * math.pi) * 1.4
            painter.setBrush(QBrush(QColor(255, 255, 255, 160)))
            painter.drawEllipse(QPointF(fill_end + off, cy - off), 1.2, 1.2)

        # --- right text zone ---
        painter.setFont(f)
        tx = text_left
        if elided:
            painter.setPen(Colors.TEXT_PRIMARY)
            painter.drawText(QRectF(tx, band.top(), chev_x - tx, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             head_text)
        else:
            if show_tier:
                painter.setPen(tier_col)
                tw = fm.horizontalAdvance(tier_name)
                painter.drawText(QRectF(tx, band.top(), tw + 2, band.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 tier_name)
                tx += tw
                painter.setPen(QColor(96, 96, 130))
                sw = fm.horizontalAdvance(sep)
                painter.drawText(QRectF(tx, band.top(), sw + 2, band.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 sep)
                tx += sw
            pre = "faster than "
            painter.setPen(Colors.TEXT_SECONDARY)
            pw = fm.horizontalAdvance(pre)
            painter.drawText(QRectF(tx, band.top(), pw + 2, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, pre)
            tx += pw
            pctt = f"{pct}%"
            painter.setPen(ACCENT)
            painter.drawText(QRectF(tx, band.top(), fm.horizontalAdvance(pctt) + 4, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, pctt)

        # --- chevron (click affordance) ---
        painter.setFont(Fonts.body())
        painter.setPen(ACCENT if self._speed_hover else QColor(120, 120, 150))
        painter.drawText(QRectF(chev_x, band.top() - 1, chev_w, band.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter, "›")
        painter.restore()

    def _paint_speed_bolt(self, painter, cx, cy, h, color):
        """A crisp lightning bolt — the speed emblem in the velocity band's left
        slot. Pure path, fixed-size, so it never clips or reads as anything but
        'speed'."""
        w = h * 0.52
        left, top = cx - w / 2.0, cy - h / 2.0
        pts = [(0.60, 0.00), (0.08, 0.56), (0.44, 0.56),
               (0.30, 1.00), (0.92, 0.40), (0.56, 0.40)]
        p = QPainterPath()
        for i, (fx, fy) in enumerate(pts):
            pt = QPointF(left + fx * w, top + fy * h)
            p.moveTo(pt) if i == 0 else p.lineTo(pt)
        p.closeSubpath()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QLinearGradient(left, top, left, top + h)
        grad.setColorAt(0.0, QColor(color).lighter(140))
        grad.setColorAt(1.0, QColor(color).darker(115))
        painter.setPen(QPen(QColor(color).lighter(150), 0.8))
        painter.setBrush(QBrush(grad))
        painter.drawPath(p)
        painter.restore()

    def _paint_emblem(self, painter, rect, color, elite):
        """A small hexagonal rank crest with an inner gem, glow + shimmer for
        elite tiers."""
        cx, cy = rect.center().x(), rect.center().y()
        r = rect.width() / 2.0

        def hexagon(radius):
            p = QPainterPath()
            for i in range(6):
                ang = math.radians(60 * i - 30)
                px = cx + radius * math.cos(ang)
                py = cy + radius * math.sin(ang)
                p.moveTo(px, py) if i == 0 else p.lineTo(px, py)
            p.closeSubpath()
            return p

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if elite:
            for k, alpha in ((1.8, 46), (2.8, 22)):
                gc = QColor(color)
                gc.setAlpha(alpha)
                painter.setPen(QPen(gc, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(hexagon(r + k))

        body = hexagon(r)
        grad = QLinearGradient(cx, rect.top(), cx, rect.bottom())
        grad.setColorAt(0.0, QColor(color).lighter(155))
        grad.setColorAt(1.0, QColor(color).darker(135))
        painter.setPen(QPen(QColor(color).lighter(165), 1.2))
        painter.setBrush(QBrush(grad))
        painter.drawPath(body)

        # inner gem
        g = r * 0.46
        gem = QPainterPath()
        gem.moveTo(cx, cy - g)
        gem.lineTo(cx + g * 0.72, cy)
        gem.lineTo(cx, cy + g)
        gem.lineTo(cx - g * 0.72, cy)
        gem.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 165))
        painter.drawPath(gem)

        # shimmer sweep (clipped to the hexagon)
        if elite:
            painter.setClipPath(body)
            reach = rect.width() + rect.height()
            sx = rect.left() - rect.height() + self._shimmer * (reach + rect.height())
            bw = 5.0
            sweep = QPainterPath()
            sweep.moveTo(sx, rect.bottom())
            sweep.lineTo(sx + bw, rect.bottom())
            sweep.lineTo(sx + bw + rect.height(), rect.top())
            sweep.lineTo(sx + rect.height(), rect.top())
            sweep.closeSubpath()
            painter.setBrush(QColor(255, 255, 255, 95))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(sweep)
        painter.restore()

    # ---- Trust Seal rendering (The Ledger) ----

    @staticmethod
    def _shield_path(cx, top, w, h):
        """A heraldic shield: rounded-top rectangle tapering to a bottom point."""
        p = QPainterPath()
        left, right = cx - w / 2, cx + w / 2
        r = 2.0
        p.moveTo(left, top + r)
        p.quadTo(left, top, left + r, top)
        p.lineTo(right - r, top)
        p.quadTo(right, top, right, top + r)
        p.lineTo(right, top + h * 0.52)
        p.quadTo(right, top + h * 0.82, cx, top + h)
        p.quadTo(left, top + h * 0.82, left, top + h * 0.52)
        p.closeSubpath()
        return p

    def _paint_trust_seal(self, painter, box, grade, hover=False):
        """A small painted shield carrying the provider's letter grade plus
        rim notches for active offenses. Fixed geometry (independent of font)
        so it never clips; the grade letter is font-metric-centered."""
        color = QColor(grade.color)
        cx = box.center().x()
        sw, sh = 11.0, 14.0
        top = box.center().y() - sh / 2
        shield = self._shield_path(cx, top, sw, sh)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # S-tier (and hover) earn a soft outer glow — calm, not animated.
        if grade.is_top or hover:
            glow = QColor(color)
            glow.setAlpha(70 if hover else 45)
            painter.setPen(QPen(glow, 2.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._shield_path(cx, top - 1, sw + 2.2, sh + 2.2))

        body = QColor(color)
        body.setAlpha(64 if hover else 42)
        painter.setBrush(QBrush(body))
        painter.setPen(QPen(color, 1.3))
        painter.drawPath(shield)

        # Offense notches: small card-colored bites out of the right rim, one
        # per active harm (capped at 4). A clean S/A seal has none.
        n = grade.notch_count
        if n:
            painter.setBrush(QBrush(Colors.BG_CARD))
            painter.setPen(Qt.PenStyle.NoPen)
            right = cx + sw / 2
            for i in range(n):
                ny = top + 2.5 + i * 2.6
                nick = QPainterPath()
                nick.moveTo(right + 0.5, ny)
                nick.lineTo(right - 2.0, ny + 1.1)
                nick.lineTo(right + 0.5, ny + 2.2)
                nick.closeSubpath()
                painter.fillPath(nick, QBrush(Colors.BG_CARD))

        # Grade letter, optically centered in the shield's BODY (above the
        # point) via a rect + alignment — a baseline calc sat it on the point.
        lf = QFont(Fonts.tiny())
        lf.setBold(True)
        lf.setPointSize(8)
        painter.setFont(lf)
        painter.setPen(QColor(color).lighter(125))
        painter.drawText(QRectF(cx - sw / 2.0, top, sw, sh - 3.0),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         grade.grade)
        painter.restore()

    def arena_html(self) -> str:
        """Fighter Card content for the popup: base stats, lifetime medals,
        and the full category ladder with ELO bars."""
        e = self._benchmark
        if e is None:
            return ""
        tier_name, tier_hex = e.tier
        name = html.escape(self._display_model_name())
        out = [f"<div style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</div>"]
        sig = e.signature
        if sig:
            out.append(
                f"<div style='color:{tier_hex};font-size:9pt;font-weight:bold;"
                f"margin-bottom:2px;'>◆ {tier_name} &nbsp;·&nbsp; #{sig.rank} in "
                f"{html.escape(sig.category).upper()} &nbsp;·&nbsp; {sig.elo} ELO</div>")
        out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:8px;'>"
                   "DesignArena ranks · Artificial Analysis indices</div>")

        stats = [(k, v) for k, v in (
            ("Intelligence", e.intelligence), ("Coding", e.coding),
            ("Agentic", e.agentic)) if v is not None]
        if stats:
            cells = "".join(
                f"<td style='padding:2px 16px 2px 0;'>"
                f"<span style='color:#64648c;font-size:8pt;'>{k}</span><br>"
                f"<span style='color:#f0f0ff;font-size:11pt;font-weight:bold;'>{v:.1f}</span></td>"
                for k, v in stats)
            out.append(f"<table cellspacing='0' style='margin-bottom:8px;'><tr>{cells}</tr></table>")

        if e.battles:
            out.append(
                f"<div style='font-size:9pt;margin-bottom:8px;'>"
                f"<span style='color:#ffd23f;'>&#127942; {e.golds:,}</span> &nbsp; "
                f"<span style='color:#c8c8e0;'>&#129352; {e.silvers:,}</span> &nbsp; "
                f"<span style='color:#cd8d5a;'>&#129353; {e.bronzes:,}</span> &nbsp; "
                f"<span style='color:#64648c;'>across {e.battles:,} duels</span></div>")

        elos = [s.elo for s in e.standings] or [1]
        lo, hi = min(elos), max(elos)
        span = max(1, hi - lo)
        rows = []
        for s in e.standings:
            nbars = 3 + int(15 * (s.elo - lo) / span)
            bar = "&nbsp;" * nbars
            medal = {1: "&#127942; ", 2: "&#129352; ", 3: "&#129353; "}.get(s.rank, "")
            rows.append(
                f"<tr>"
                f"<td style='padding:2px 12px 2px 0;color:#e6e6ff;white-space:nowrap;'>{medal}{html.escape(s.category)}</td>"
                f"<td style='padding:2px 10px;color:#a0a0c8;white-space:nowrap;' align='right'>"
                f"#{s.rank}<span style='color:#5a5a78;'>/{s.field_size}</span></td>"
                f"<td style='padding:2px 8px;'><span style='background-color:{tier_hex};"
                f"color:{tier_hex};'>{bar}</span></td>"
                f"<td style='padding:2px 0 2px 6px;color:{tier_hex};font-weight:bold;white-space:nowrap;' align='right'>{s.elo}</td>"
                f"<td style='padding:2px 0 2px 12px;color:#64648c;white-space:nowrap;' align='right'>{s.win_rate:.0f}%</td>"
                f"</tr>")
        out.append(
            f"<table cellspacing='0' style='border-spacing:0;'>"
            f"<tr style='color:#64648c;font-size:8pt;'>"
            f"<th align='left' style='padding:2px 12px 4px 0;'>CATEGORY</th>"
            f"<th align='right' style='padding:2px 10px 4px;'>RANK</th><th></th>"
            f"<th align='right' style='padding:2px 0 4px 6px;'>ELO</th>"
            f"<th align='right' style='padding:2px 0 4px 12px;'>WIN</th></tr>"
            f"{''.join(rows)}</table>")
        return "".join(out)

    # ---- The Time Slip (speed dossier popup) ----

    def speed_html(self) -> str:
        """The 'Time Slip' dossier: both speed axes laid out with ELO-style bars,
        the per-axis champion provider + its price, and a plain-English verdict
        when the two axes diverge — so the band's comet-vs-tick gap is auditable."""
        st = self._speed
        if st is None:
            return ""
        r = st.ranking
        tier_name, tier_hex = st.tier
        tcol = _safe_color(tier_hex)
        name = html.escape(self._display_model_name())
        fs = st.field_size or 0

        def pctn(p):
            return f"{round(p * 100)}" if p is not None else "—"

        def bar(p, color):
            n = 2 + int(20 * (p if p is not None else 0))
            return (f"<span style='background-color:{color};color:{color};'>"
                    f"{'&nbsp;' * n}</span>")

        def money(v):
            return f"${v:g}/M" if v is not None else ""

        def rank_cell(rank):
            return f"#{rank}/{fs}" if rank else "—"

        out = [f"<div style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</div>"]
        out.append(
            f"<div style='color:{tcol};font-size:9pt;font-weight:bold;margin-bottom:2px;'>"
            f"&#9656; {html.escape(tier_name)} &nbsp;·&nbsp; faster than "
            f"{pctn(st.throughput_pct)}% of the field</div>")
        out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:8px;'>"
                   f"Speed percentile · {fs}-model field · live from openrouter.ai</div>")

        tps = f"{r.p50_throughput:.0f} t/s" if r.p50_throughput is not None else "—"
        if r.p50_latency is None:
            lat = "—"
        elif r.p50_latency >= 1000:
            lat = f"{r.p50_latency / 1000:.1f}s"
        else:
            lat = f"{r.p50_latency:.0f} ms"

        def gauge_row(label, value, p, rank, color):
            return (
                f"<tr>"
                f"<td style='padding:2px 12px 2px 0;color:#a0a0c8;white-space:nowrap;'>{label}</td>"
                f"<td style='padding:2px 10px 2px 0;color:#f0f0ff;font-weight:bold;white-space:nowrap;' align='right'>{value}</td>"
                f"<td style='padding:2px 8px;'>{bar(p, color)}</td>"
                f"<td style='padding:2px 0 2px 8px;color:{color};font-weight:bold;white-space:nowrap;' align='right'>{pctn(p)}th</td>"
                f"<td style='padding:2px 0 2px 10px;color:#64648c;white-space:nowrap;' align='right'>{rank_cell(rank)}</td>"
                f"</tr>")

        out.append(
            "<table cellspacing='0' style='border-spacing:0;margin-bottom:8px;'>"
            + gauge_row("STREAM SPEED", tps, st.throughput_pct, st.throughput_rank, "#5bd2ff")
            + gauge_row("FIRST TOKEN", lat, st.latency_pct, st.latency_rank, "#ffc700")
            + "</table>")

        champs = []
        if r.best_throughput_provider:
            champs.append(
                f"<div style='color:#c8c8e0;font-size:9pt;'>"
                f"<span style='color:#5bd2ff;'>&#9650; Fastest stream:</span> "
                f"{html.escape(str(r.best_throughput_provider))} "
                f"<span style='color:#64648c;'>{money(r.best_throughput_price)}</span></div>")
        if r.best_latency_provider:
            champs.append(
                f"<div style='color:#c8c8e0;font-size:9pt;'>"
                f"<span style='color:#ffc700;'>&#9889; Fastest first token:</span> "
                f"{html.escape(str(r.best_latency_provider))} "
                f"<span style='color:#64648c;'>{money(r.best_latency_price)}</span></div>")
        out.append("".join(champs))

        verdict = self._speed_verdict(st)
        if verdict:
            out.append(
                f"<div style='margin-top:8px;color:{tcol};font-size:8.5pt;"
                f"font-style:italic;'>{html.escape(verdict)}</div>")
        out.append("<div style='margin-top:6px;color:#64648c;font-size:8pt;'>"
                   "Percentile = share of the live field this model out-paces "
                   "· refreshed every 5 min</div>")
        return "".join(out)

    def _speed_verdict(self, st) -> str:
        """Plain-English read of the throughput-vs-latency split."""
        tp, lp = st.throughput_pct, st.latency_pct
        if tp is None or lp is None:
            return ""
        diff = tp - lp
        if diff >= 0.25:
            return "Streams fast, but slower to first token."
        if diff <= -0.25:
            return "Quick to first token, but mid-pack streaming."
        if tp >= 0.75 and lp >= 0.75:
            return "Fast both ways — quick to first token and fast streaming."
        return ""

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
        # Flush-left with the card border / section titles / search bar, so
        # the whole section shares one clean left edge. (The model rows sit
        # further in to clear the ★ best-provider marker — the column label
        # reading from the container edge looks intentional, not floating.)
        name_x = 0

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
        theme_controller.changed.connect(self.update)

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
        accent = theme_controller.accent()
        grad = QLinearGradient(0, pad_top, 0, pad_top + chart_h)
        c_top = QColor(accent)
        c_top.setAlpha(80)
        c_bot = QColor(accent)
        c_bot.setAlpha(4)
        grad.setColorAt(0, c_top)
        grad.setColorAt(1, c_bot)
        painter.fillPath(fill, QBrush(grad))

        # Line on top
        painter.setPen(QPen(accent, 1.8, Qt.PenStyle.SolidLine,
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
        painter.setBrush(QBrush(theme_controller.accent()))
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