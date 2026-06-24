"""
OpenRouter Pulse - Custom Widgets
Hand-drawn gauges, sparklines, stat cards, status badges.
"""
import datetime
import html
import logging
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
    QBuffer, QByteArray, QIODevice,
)
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QConicalGradient,
    QRadialGradient, QLinearGradient, QPainterPath, QFont,
    QFontMetrics, QPolygonF, QPixmap, QImage,
)
import base64

from theme import Colors, Fonts
import theme_controller
import spend_palette

log = logging.getLogger("pulse.widgets")
_door_log = logging.getLogger("pulse.threshold")   # #5 door-resolution INFO line
_waterline_log = logging.getLogger("pulse.waterline")  # #6 hidden-fee-classes INFO line


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


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    """Linear blend a→b by t∈[0,1]. Returns a fresh QColor (callers that need
    the allocation-free hot path must precompute, not call this in paint)."""
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


# ---------------------------------------------------------------------------
#  THE TAPE (#7) — week-over-week momentum stamp + slope helpers (pure)
# ---------------------------------------------------------------------------
# `change` is an UNBOUNDED week-over-week request-volume FRACTION (-1.0 = -100%
# dying, 0.50 = +50% riser, 247 = +24700% new entrant). The honest-magnitude
# rule (decision A): print a literal % only in the normal band; switch to a "Nx"
# multiplier for explosive risers (so we never stamp a meaningless "+24700%"),
# clamp the multiplier at 999x, and collapse a near-zero change to a flat "~".
# Module-level + pure so a unit test can pin the EXACT format output without a
# QWidget. f"{change:+.0%}" rounding is whatever Python's %-format produces
# (e.g. -0.975 → "-98%"); the test asserts that literal, never a guess.
TREND_FLAT_EPS = 0.03      # |change| below this reads as flat (a centered dash)
TREND_EXPLOSIVE = 5.0      # change above this is "off the chart" → "Nx" + ghost


def _trend_stamp(change) -> str:
    """The torn-ticker delta stamp for a week-over-week change fraction."""
    if change is None:
        return ""
    if abs(change) < TREND_FLAT_EPS:
        return "~"
    if change > TREND_EXPLOSIVE:
        return f"+{min(round(change), 999)}x"
    return f"{change:+.0%}"


def _trend_slope_sign(change) -> int:
    """+1 riser / -1 faller / 0 flat — the geometric tell the trace encodes.
    Thresholds match TREND_FLAT_EPS so the stamp and the slope never disagree
    (a "~" stamp always pairs with a 0 slope, i.e. a centered dash)."""
    if change is None:
        return 0
    if change > TREND_FLAT_EPS:
        return 1
    if change < -TREND_FLAT_EPS:
        return -1
    return 0


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
#  THE PULSE — the dossier's painted 73-bar Vitals strip (#3)
# ---------------------------------------------------------------------------
class UptimeStripWidget(QWidget):
    """The Vitals dossier's hero: 73 hourly bars rendered at full resolution
    with a continuous green→amber→crimson depth ramp, a day axis, a "now"
    marker, and the worst hour called out. This is the ONLY surface that can
    show all 73 hours at once (the row glyph is a 36px summary). Rendered to a
    QPixmap and embedded as a data-URI <img> in the single-QLabel ProviderPopup
    (decision B — keeps the popup's single-label/adjustSize contract intact)."""

    STRIP_W = 292
    STRIP_H = 64
    PAD = 8
    CRIMSON = QColor(224, 70, 60)   # a darker "wound" red, dossier-only

    def __init__(self, hist, parent=None):
        super().__init__(parent)
        self._hist = hist
        self.setFixedSize(self.STRIP_W, self.STRIP_H)
        # Test introspection.
        self._strip_bar_xs = []
        self._strip_worst_x = None

    def _bar_color(self, v):
        """The continuous depth ramp: green(>=99) → yellow(95-99) → crimson(<95,
        deepest at <=40). The dossier keeps the continuous float — it is NOT a
        binary heat cell."""
        if v >= 99.0:
            return QColor(Colors.GREEN)
        if v >= 95.0:
            t = (99.0 - v) / 4.0
            return _lerp_color(QColor(Colors.GREEN), QColor(Colors.YELLOW), t)
        t = (95.0 - v) / 55.0    # 95→0, 40→1
        return _lerp_color(QColor(Colors.YELLOW), self.CRIMSON, t)

    def render_pixmap(self) -> QPixmap:
        pm = QPixmap(self.STRIP_W, self.STRIP_H)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        hist = self._hist
        vals = hist.values if hist else []
        n = len(vals)
        pad = self.PAD
        axis_font = Fonts.tiny()
        fm = QFontMetrics(axis_font)
        axis_h = fm.height() + 2
        chart_top = 2.0
        chart_bottom = self.STRIP_H - axis_h - 2.0
        chart_h = chart_bottom - chart_top
        span = max(1, n - 1)
        inner_w = self.STRIP_W - 2 * pad

        # 1. dim baseline frame.
        frame = QColor(Colors.TEXT_MUTED)
        frame.setAlpha(70)
        p.setPen(QPen(frame, 1))
        p.drawLine(QPointF(pad, chart_bottom), QPointF(self.STRIP_W - pad, chart_bottom))

        if n == 0:
            return
        bar_w = max(2.0, inner_w / n - 1.0)
        worst = hist.worst
        # Only mark a worst bar when it's a REAL dip (<99%) — a flawless strip
        # has no wound to call out (mirrors the row's outage-only worst dot), so
        # crimson never appears on an all-green record.
        mark_worst = bool(worst and worst[1] < 99.0)
        worst_date = worst[0] if mark_worst else None

        def x_of(i):
            return pad + i * (inner_w / span)

        self._strip_bar_xs = [x_of(i) for i in range(n)]
        self._strip_worst_x = None

        # 2. day-boundary ticks: drop a faint tick + a "Nd"/"now" label wherever
        #    the YYYY-MM-DD prefix changes.
        prev_day = None
        p.setFont(axis_font)
        for i, (date_str, _v) in enumerate(hist.points):
            day = (date_str or "")[:10]
            if day and day != prev_day:
                prev_day = day
                x = x_of(i)
                tick = QColor(Colors.TEXT_MUTED)
                tick.setAlpha(60)
                p.setPen(QPen(tick, 1))
                p.drawLine(QPointF(x, chart_top), QPointF(x, chart_bottom))

        # 3. the 73 bars.
        for i, (date_str, v) in enumerate(hist.points):
            x = x_of(i)
            if v is None:
                # a gap: a hollow dotted bar in muted grey, NEVER red.
                col = QColor(Colors.TEXT_MUTED)
                p.setPen(QPen(col, 1, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawLine(QPointF(x, chart_bottom), QPointF(x, chart_top + chart_h * 0.5))
                continue
            if v >= 90.0:
                frac = (v - 90.0) / 10.0
                frac = 0.0 if frac < 0.0 else 1.0 if frac > 1.0 else frac
                top_y = chart_bottom - frac * chart_h
            else:
                top_y = chart_bottom - 0.10 * chart_h   # a tiny nub for a catastrophe
            col = self._bar_color(v)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(col))
            p.drawRect(QRectF(x - bar_w / 2, top_y, bar_w, chart_bottom - top_y))
            if worst_date is not None and date_str == worst_date:
                self._strip_worst_x = x
                # crimson outline + a caret above the worst bar.
                p.setPen(QPen(self.CRIMSON, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(x - bar_w / 2, top_y, bar_w, chart_bottom - top_y))
                caret = QPolygonF([
                    QPointF(x, chart_top + 1), QPointF(x - 3, chart_top - 3),
                    QPointF(x + 3, chart_top - 3)])
                p.setBrush(QBrush(self.CRIMSON))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawPolygon(caret)

        # 4. the rightmost bar ("now") gets a cyan frame so "now" is unambiguous.
        if n:
            x = x_of(n - 1)
            p.setPen(QPen(QColor(Colors.CYAN), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(x - bar_w / 2 - 1, chart_top, bar_w + 2, chart_h))


# ---------------------------------------------------------------------------
#  THE TAPE — the dossier's 2-point "last 7d" momentum ramp (#7)
# ---------------------------------------------------------------------------
class TrendRampWidget(QWidget):
    """The Tape dossier's hero: a HONEST 2-point ramp (last-week index → now)
    drawn from the SINGLE week-over-week `change` fraction. We have ONE delta,
    so we draw exactly two anchored points (NOT a fabricated multi-point series),
    in the same amber/violet lane as the card cartouche. Rendered to a QPixmap +
    embedded as a data-URI <img> in the single-QLabel ProviderPopup, mirroring
    UptimeStripWidget."""

    STRIP_W = 292
    STRIP_H = 56
    PAD = 10

    def __init__(self, change, line_color: QColor, parent=None):
        super().__init__(parent)
        self._change = change
        self._line = QColor(line_color)
        self.setFixedSize(self.STRIP_W, self.STRIP_H)

    def render_pixmap(self) -> QPixmap:
        pm = QPixmap(self.STRIP_W, self.STRIP_H)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        pad = self.PAD
        left, right = pad, self.STRIP_W - pad
        top, bottom = 6.0, self.STRIP_H - 8.0
        midy = (top + bottom) / 2.0

        # dim baseline frame at the "last week" index level (the mid-line).
        frame = QColor(Colors.TEXT_MUTED); frame.setAlpha(70)
        p.setPen(QPen(frame, 1))
        p.drawLine(QPointF(left, midy), QPointF(right, midy))

        ch = self._change if self._change is not None else 0.0
        # Map the ratio to a slope: tail anchored at the index mid-line, head
        # offset by the magnitude (capped) — up for a riser, down for a faller.
        mag = min(abs(ch), 1.0)
        dy = (bottom - midy) * 0.92 * mag
        tail = QPointF(left, midy)
        head = QPointF(right, midy - dy if ch >= 0 else midy + dy)

        p.setPen(QPen(self._line, 2.0, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(tail, head)
        # anchor dots
        p.setPen(Qt.PenStyle.NoPen)
        dim = QColor(self._line); dim.setAlpha(150)
        p.setBrush(QBrush(dim)); p.drawEllipse(tail, 2.6, 2.6)
        p.setBrush(QBrush(self._line)); p.drawEllipse(head, 3.4, 3.4)


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
    DOOR_H = 28         # #5 THE THRESHOLD "cheapest door" band (only when a door)
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
    door_clicked = Signal(str, QPointF)    # #5 door band clicked -> Threshold dossier
    uptime_clicked = Signal(str, str, QPointF)  # (model_id, ep_ident, anchor) -> Vitals
    fees_clicked = Signal(str, str, QPointF)  # #6 (model_id, ep_ident, anchor) -> Waterline
    trend_clicked = Signal(str, QPointF)   # #7 THE TAPE clicked -> week-over-week dossier
    drift_clicked = Signal(str, QPointF)   # #8 THE FAULT LINE clicked -> Seismograph dossier

    # ---- #7 THE TAPE (the torn-ticker momentum cartouche) geometry constants ----
    # A compact "ripped off the wire" cartouche pinned in the header RIGHT gutter
    # (the slot the ★ best-chip vacated — it relocates inline after the name).
    # Adds NO height (TAPE_H=15 < HEADER_H=28 slack); set_trend never reflows.
    TAPE_H = 15
    TAPE_TRACE_W = 16           # the sloped 3-tick trace slot width
    TAPE_DOT_R = 2.2            # head-dot radius (the latest tick)
    TAPE_NOTCH = 3             # torn-paper notch triangle size on the pill's left
    TAPE_HPAD = 10             # pill horizontal padding
    TAPE_TRACE_GAP = 4         # gap from the trace to the stamp
    GAP_CHIP_TO_ICON = 10      # tape right edge sits this far left of the ⓘ icon
    GAP_NAME_TO_CHIP = 12      # name↔inline-chip and tape↔name reservation gap
    # Lane colors (decision E) — amber riser / violet faller / grey flat, NEVER
    # green (Pulse owns it) or red (Pulse outage). Region-separated from #5's
    # brass-amber band by living in the header gutter.
    TAPE_AMBER = QColor(0xF4, 0xB7, 0x40)        # warm "hot money" riser
    TAPE_AMBER_HOT = QColor(0xFF, 0xD0, 0x71)    # brighter, explosive riser
    TAPE_VIOLET = QColor(0x9B, 0x8C, 0xCB)       # muted "cooling off" faller (light)
    TAPE_VIOLET_DK = QColor(0x7A, 0x6E, 0x9E)    # faller, deep end

    # ---- THE PULSE (#3 — the 73h uptime cardiogram) geometry constants ----
    # A health-keyed heartbeat painted in the existing UPTIME_W column. Adds NO
    # height; lives in the ROW_H=22 row like latency/price. See uptime_spec.md.
    UPTIME_W = 36
    PULSE_AMP_UP = 2.0          # calm systole height above baseline
    PULSE_FLOOR_MARGIN = 3.0    # px of headroom below baseline before the row edge
    PULSE_DIP_FLOOR = 31.0      # the real worst (~31%) pins a plunge to the floor
    PULSE_DIP_THRESH = 99.0     # an hour below this is a "dip" (matches outage_hours)
    # A tiny systole shape so a clean record reads as ALIVE, not a flat bar.
    # Liveness-only — deliberately small so it never implies a data event.
    PULSE_BEAT = (0.0, 0.0, 0.0, 1.0, 0.35, 0.0, 0.0)

    # ---- #8 THE FAULT LINE (price-drift seismograph) geometry constants ----
    # A vertical zig-zag crack etched down the card's LEFT EDGE (x in [2,9], a
    # 7px channel LEFT of the seal column at PAD_X=14 / _icon_col_cx=21), painted
    # over the rounded BG path and clipped to it, ONLY when a drift exists. Plus
    # per-row tremor ticks at PAD_X-4 (nudged to PAD_X-2 on a best row, to clear
    # the gold accent). Adds ZERO height (decision E): set_drift never reflows.
    FAULT_X_MIN = 2.0           # crack channel left bound
    FAULT_X_MAX = 9.0           # crack channel right bound (= PAD_X-5)
    FAULT_Y_MARGIN = 12.0       # vertical inset from top/bottom (inside the r=10 corners)
    FAULT_EPI_R = 4.0           # epicenter diamond half-size
    TICK_W = 2.0                # per-row tremor tick width
    # Two-pole lane (decision G): seismic-amber (adverse) / quartz-violet
    # (favorable). Warmer/oranger than #5's brass-amber and #7's hot-amber, and
    # region-separated (card EDGE, not band / header gutter).
    FAULT_AMBER = QColor(0xff, 0x9e, 0x3d)    # ADVERSE — price rose / deranked
    FAULT_VIOLET = QColor(0xb0, 0x7c, 0xff)   # FAVORABLE — price fell / cheaper appeared

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
        # #5 THE THRESHOLD — the "cheapest door" band (3rd band, after Speed).
        # _door is a DoorResolution or None (None → band paints nothing). The
        # accent + green flag are resolved ONCE in set_door so paint allocates
        # nothing per frame (the QPolygonF leaf is built once here too).
        self._door = None                # api_client.DoorResolution | None
        self._show_door = True           # gated by settings.show_door (main.py)
        self._door_hit_rect = QRectF()
        self._door_hover = False
        self._door_accent = QColor(0xe0, 0xa1, 0x3a)   # amber; emerald when green
        self._door_green = False
        # Render introspection (set during paint) — measured by the test.
        self._door_leaf_poly = QPolygonF()   # the perspective door-leaf trapezoid
        self._door_text_left = 0.0           # x where the lintel text starts
        self._door_chev_x = 0.0              # right chevron x
        # THE PULSE (#3) — per-endpoint 73h uptime histories, keyed by the SAME
        # _ep_ident(ep) the trust seals use so the right history lands on the
        # right row across refreshes. Empty → every row keeps the legacy %-chip.
        self._uptime = {}                # {ep_ident: UptimeHistory}
        self._uptime_alive = False       # any pinned endpoint is flawless → opt-in heartbeat
        self._pulse_hits = []            # [(QRectF, ident, accent_hex)] per row
        self._pulse_hover_ident = None
        self._pulse_alpha = QColor(Colors.GREEN)   # preallocated; mutated for the heartbeat
        # Per-row measured geometry, keyed by ident, built ONCE in set_uptime so
        # the paint hot path only strokes cached objects (GC-disabled invariant).
        self._pulse_cache = {}           # {ident: measured-pulse dict}
        # Render introspection (set during paint of the LAST row drawn / per row
        # during build) — measured by the deterministic test.
        self._pulse_glyph_rect = QRectF()
        self._pulse_baseline_y = 0.0
        self._pulse_worst_pt = None      # QPointF | None
        self._pulse_dip_xs = []          # list[float]
        self._pulse_has_outage = False
        self._pulse_avg = None
        # #6 THE WATERLINE — the hidden-cost iceberg under each price. Per-row,
        # ZERO height, ZERO column shift (lives in the price cell's sub-baseline
        # slack). The fee CLASSES + submerged depth are resolved ONCE per ident
        # in set_fees (mirroring _pulse_cache) so the paint hot path only fills
        # cached rects (GC-disabled / allocation-free-paint invariant).
        self._show_fees = True            # gated by settings.show_hidden_fees
        self._waterline_depth = {}        # {ident: submerged fraction 0..1}
        self._waterline_fee_classes = {}  # {ident: frozenset of class names}
        self._waterline_buoy = {}         # {ident: bool} implicit-caching support
        self._waterline_hits = []         # [(QRectF, ident, accent_hex)] clickable rows
        self._waterline_buoy_rects = {}   # {ident: QRectF} recorded buoy ring (test introspection)
        self._waterline_hover_ident = None  # price cell under the cursor (for the hand cursor)
        # #7 THE TAPE — week-over-week momentum cartouche in the header gutter.
        # _trend is the raw change FRACTION (or None → paints nothing, empty hit
        # rect). The 3-point sloped trace QPolygonF + the slope sign + stamp are
        # built ONCE in set_trend (allocation-free paint, GC-disabled invariant);
        # paint only positions/strokes cached objects. NO height (decision D).
        self._trend = None                # change float | None
        self._trend_explosive = False     # change > 5 → ghost trace + live-wire shimmer
        self._trend_stamp = ""            # measured-once delta stamp ("+50%"/"+248x"/"~")
        self._trend_hover = False
        self._tape_hit_rect = QRectF()    # introspection: the clickable cartouche box
        self._tape_trace_pts = []         # introspection: 3 QPointF (UNIT shape, 0..1)
        self._tape_slope_sign = 0         # introspection: +1 riser / -1 faller / 0 flat
        # #8 THE FAULT LINE — the price-drift seismograph crack on the card edge.
        # _drift is a price_drift.DriftResult or None (None / magnitude 0 ->
        # paint NOTHING, zero pixels, zero height — the silent-degrade contract,
        # decision E). The crack geometry (zig-zag QPainterPath + per-row tick
        # rects + epicenter) is MEASURED ONCE in _measure_drift, cached in
        # _drift_geom (cleared in set_drift), so the paint hot path only strokes
        # cached objects (allocation-free paint, GC-disabled invariant).
        self._drift = None                # price_drift.DriftResult | None
        self._show_drift = True           # gated by settings.show_drift (main.py)
        self._drift_fresh = False         # rides the shimmer for ONE refresh, then static
        self._drift_geom = None           # measured-once geometry dict | None
        self._drift_hits = []             # [(QRectF, ident)] tremor-tick + edge-band click targets
        self._drift_hover = False         # cursor-only (no repaint), mirrors the waterline
        self._drift_alpha = QColor(self.FAULT_AMBER)  # preallocated; alpha breathes when fresh
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
        door = self.DOOR_H if self._door is not None else 0
        # Generalized multi-band gap math (decision D — #5 makes this 3 bands and
        # this MUST be right for every future band): one BAND_GAP sits BETWEEN
        # each pair of present bands, so N present bands take (N-1) gaps. ROWS_GAP
        # sits below the last band before the provider rows iff any band is shown.
        bands = (crest > 0) + (speed > 0) + (door > 0)
        inter = self.BAND_GAP * max(0, bands - 1)
        has_band = bands > 0
        below = self.ROWS_GAP if has_band else 0    # gap before the provider rows
        h = (self.HEADER_H + crest + speed + door + inter + below
             + max(1, rows) * self.ROW_H + self.PAD_Y * 2)
        self.setFixedHeight(h)

    # ---- Shimmer (shared by the Arena crest + the elite speed comet) ----

    def _wants_shimmer(self) -> bool:
        # The flawless-uptime "earned heartbeat" rides the SAME 55ms timer as
        # the Arena sweep / elite speed comet — no new QTimer (decision C). #7's
        # explosive-riser "live wire" (ghost-trace pulse + breathing head-dot)
        # reuses it too via _trend_explosive — ordinary risers/fallers stay static.
        # #8: a FRESH drift (just detected, unacknowledged) breathes its
        # epicenter diamond + Δ glyph on the SAME timer (decision E) — once
        # acknowledge() clears _drift_fresh the crack goes static (still drawn).
        return (self._arena_elite or self._speed_elite or self._uptime_alive
                or self._trend_explosive or self._drift_fresh)

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

    # ---- #7 THE TAPE (the torn-ticker momentum cartouche) ----

    def set_trend(self, change):
        """change: the week-over-week request-volume FRACTION (a float) for this
        model, or None when the model isn't in the ranked rows / the fetch
        failed. None paints nothing and empties the hit rect (silent degrade).

        Stores the change + the explosive flag, MEASURES the stamp, and builds
        the 3-point sloped trace ONCE here as a UNIT-space (0..1) shape so the
        paint hot path only positions + strokes cached objects (allocation-free
        paint, GC-disabled invariant). NEVER calls _update_height — the cartouche
        lives inside the header band's slack and adds no height (decision D)."""
        self._trend = change
        self._trend_explosive = bool(change is not None and change > TREND_EXPLOSIVE)
        self._trend_stamp = _trend_stamp(change)
        sign = _trend_slope_sign(change)
        self._tape_slope_sign = sign
        # Build the UNIT trace (x,y in [0,1]; y grows DOWNWARD in screen space, so
        # a riser must DESCEND in y from tail→head). Slope steepness scales with
        # the magnitude, capped at a full-height swing (min(|change|,1.0)).
        mag = min(abs(change), 1.0) if change is not None else 0.0
        if sign == 0:
            # flat: a centered dash (all three points on the mid-line)
            ys = (0.5, 0.5, 0.5)
        elif sign > 0:
            # riser: staircase climbing up-right → y DESCENDS left→right
            lo, hi = 0.5 - 0.42 * mag, 0.5 + 0.42 * mag
            ys = (hi, 0.5, lo)          # tail low (high y) → head high (low y)
        else:
            # faller: descending right → y ASCENDS left→right
            lo, hi = 0.5 - 0.42 * mag, 0.5 + 0.42 * mag
            ys = (lo, 0.5, hi)          # tail high (low y) → head low (high y)
        self._tape_trace_pts = [QPointF(0.0, ys[0]),
                                QPointF(0.5, ys[1]),
                                QPointF(1.0, ys[2])]
        self._sync_shimmer()           # explosive risers wake the live-wire timer
        self.update()                  # NO _update_height (decision D)

    def has_trend(self) -> bool:
        return self._trend is not None

    def _trend_lane(self):
        """The lane colors for the current trend (decision E). Returns
        (line_color, fill_base) QColors. Riser=amber, faller=violet, flat=grey,
        explosive=brighter amber."""
        if self._trend is None:
            return QColor(Colors.TEXT_MUTED), QColor(Colors.TEXT_MUTED)
        sign = self._tape_slope_sign
        if sign > 0:
            return (QColor(self.TAPE_AMBER_HOT if self._trend_explosive else self.TAPE_AMBER),
                    QColor(self.TAPE_AMBER))
        if sign < 0:
            # deeper violet the harder the fall (toward -1.0)
            t = min(abs(self._trend), 1.0)
            return (_lerp_color(self.TAPE_VIOLET, self.TAPE_VIOLET_DK, t),
                    QColor(self.TAPE_VIOLET_DK))
        return QColor(Colors.TEXT_MUTED), QColor(Colors.TEXT_MUTED)

    def trend_accent(self) -> str:
        """Hex accent for the trend dossier border (mirrors speed_accent)."""
        if self._trend is None:
            return "#9b8ccb"
        line, _ = self._trend_lane()
        return line.name()

    # ---- #5 THE THRESHOLD (the "cheapest door" band) ----

    def set_door(self, resolution):
        """resolution: an api_client.DoorResolution or None. None (no cheaper
        door, free model, or save% rounds to 0) removes the band — the card
        paints nothing for it but DOES re-measure its height (a band toggled,
        decision C). The accent + green flag are cached here so the paint hot
        path allocates nothing per frame."""
        had = self._door is not None
        self._door = resolution
        if resolution is not None:
            self._door_green = bool(resolution.green)
            self._door_accent = QColor(_safe_color(resolution.accent, "#e0a13a"))
        else:
            self._door_green = False
            self._door_accent = QColor(0xe0, 0xa1, 0x3a)
        if (resolution is not None) != had:
            self._update_height()
        self.update()

    def has_door(self) -> bool:
        return self._door is not None

    def door_accent(self) -> str:
        """Amber normally, emerald for the green door (cheaper AND faster)."""
        from api_client import DOOR_AMBER, DOOR_EMERALD
        if self._door is None:
            return DOOR_AMBER
        return DOOR_EMERALD if self._door.green else DOOR_AMBER

    def set_show_door(self, show: bool):
        """Settings gate (show_door). When off, the band is removed (set_door
        None) and stays off until re-enabled; #5 carries no fetch to gate, so
        this is the gate point (mirrors the show_speed opt-out)."""
        show = bool(show)
        if show == self._show_door:
            return
        self._show_door = show
        self._resolve_door_from_endpoints()

    def _resolve_door_from_endpoints(self):
        """Resolve THE THRESHOLD from the endpoints/best the card already holds
        and push it through set_door. Called wherever _best/_endpoints change."""
        if not self._show_door:
            self.set_door(None)          # gated off → paint nothing (decision C)
            return
        from api_client import resolve_door
        eps = self._endpoints.endpoints if self._endpoints else []
        d = resolve_door(eps, self._best)
        self.set_door(d)
        if d is not None:
            _door_log.info(
                "threshold: %s SAVE %d%% green=%s (%s $%.3f/Mtok -> %s $%.3f/Mtok)",
                self.model_id, d.save_pct, d.green, d.from_name, d.from_mtok,
                d.cheaper_name, d.to_mtok)

    # ---- #6 THE WATERLINE (the hidden-cost iceberg under each price) ----

    def set_fees(self, model_endpoints=None):
        """Resolve + CACHE the per-row hidden-fee CLASSES, submerged depth, and
        implicit-caching buoy flag for every endpoint ONCE (mirroring the
        _pulse_cache idiom), so the paint hot path only fills cached rects
        (allocation-free / GC-disabled invariant). NEVER calls _update_height:
        the waterline lives in the existing price cell's sub-baseline slack and
        adds NO height, shifts NO column (decision F). When gated off (decision
        E/SETTING_GATE), every dict is cleared so paint draws nothing.

        `model_endpoints` is accepted for symmetry with set_endpoints/set_speed
        but the data is read from self._endpoints (the SAME payload #5 uses, no
        new fetch); pass it to seed before _endpoints is assigned if needed."""
        from api_client import (hidden_fee_classes, hidden_fee_depth,
                                 HIDDEN_MAX)
        self._waterline_depth = {}
        self._waterline_fee_classes = {}
        self._waterline_buoy = {}
        eps = (model_endpoints or self._endpoints)
        eps = eps.endpoints if eps is not None else []
        if not self._show_fees:
            self.update()
            return
        for ep in eps:
            ident = self._ep_ident(ep)
            classes = hidden_fee_classes(ep)
            depth = hidden_fee_depth(classes)
            buoy = bool(ep.supports_implicit_caching)
            # Cache even the empty/clean case (depth 0, no buoy) so the paint
            # loop can read every row from the cache without re-resolving.
            self._waterline_fee_classes[ident] = classes
            self._waterline_depth[ident] = depth
            self._waterline_buoy[ident] = buoy
            if classes or buoy:
                _waterline_log.info(
                    "waterline: %s/%s classes={%s} depth=%d/%d implicit_cache=%s",
                    self.model_id, ident,
                    ",".join(sorted(classes)) if classes else "",
                    len(classes), HIDDEN_MAX, buoy)
        self.update()

    def has_fees(self) -> bool:
        """True iff any row carries a hidden-fee class or an implicit-cache buoy
        (i.e. the waterline draws SOMETHING somewhere)."""
        if not self._show_fees:
            return False
        return (any(self._waterline_fee_classes.values())
                or any(self._waterline_buoy.values()))

    def set_show_fees(self, show: bool):
        """Settings gate (show_hidden_fees). When off the card paints no strip,
        no ticks, no buoy and records no hit rects (decision E). Mirrors
        set_show_door, but #6 adds no height so this never re-measures."""
        show = bool(show)
        if show == self._show_fees:
            return
        self._show_fees = show
        self.set_fees()

    def fees_accent(self, ident=None) -> str:
        """The dossier border accent for the waterline — the surface steel-teal
        (distinct from Speed cyan / the door amber / Pulse green)."""
        from api_client import WATERLINE_SURFACE
        return WATERLINE_SURFACE

    # ---- #8 THE FAULT LINE (the price-drift seismograph) ----

    def set_drift(self, result):
        """result: a price_drift.DriftResult or None. None OR magnitude 0 -> the
        card paints NOTHING (no crack, no ticks, no hit rects, no Δ) — the
        silent-degrade contract (decision E). A live drift stays etched until the
        dashboard pushes a None/quiet result (the next QUIET snapshot) or
        acknowledge() is followed by a quiet re-diff.

        NEVER calls _update_height (the crack is an EDGE overlay, zero height,
        decision E — explicit, like set_uptime). Clears the cached geometry so
        _measure_drift rebuilds it once on the next paint; stores the fresh flag
        and syncs the shimmer (a fresh drift breathes its epicenter/Δ)."""
        if result is not None and getattr(result, "magnitude", 0.0) <= 0.0:
            result = None                        # treat magnitude 0 as quiet
        self._drift = result if self._show_drift else None
        self._drift_fresh = bool(self._drift is not None
                                 and getattr(self._drift, "is_fresh", False))
        self._drift_geom = None                  # invalidate cache (re-measure once)
        self._sync_shimmer()
        self.update()                            # NO _update_height (decision E)

    def acknowledge(self):
        """The Seismograph dossier opened (decision E (iv)): clear the fresh
        flag so the epicenter/Δ shimmer stops, but LEAVE the crack drawn — it
        persists until the dashboard's store rolls a quiet baseline and pushes
        set_drift(None). The dashboard's store.acknowledge() is the durable half
        (writes the baseline to disk so the same drift never re-fires)."""
        if not self._drift_fresh:
            return
        self._drift_fresh = False
        self._sync_shimmer()
        self.update()

    def has_drift(self) -> bool:
        return self._drift is not None and self._drift.magnitude > 0.0

    def set_show_drift(self, show: bool):
        """Settings gate (show_drift). When off the card paints no crack, ticks
        or Δ and records no hit rects (decision G). #8 carries no fetch (it rides
        the endpoints diff), so this is the gate point; mirrors set_show_fees."""
        show = bool(show)
        if show == self._show_drift:
            return
        self._show_drift = show
        if not show:
            self.set_drift(None)                 # gated off -> paint nothing
        else:
            self._drift_geom = None
            self.update()

    def drift_accent(self) -> str:
        """The dominant pole's hex for the Seismograph dossier border (mirrors
        speed_accent): seismic-amber for adverse drift, quartz-violet for
        favorable."""
        from price_drift import FAVORABLE
        if self._drift is not None and self._drift.direction == FAVORABLE:
            return self.FAULT_VIOLET.name()
        return self.FAULT_AMBER.name()

    def _drift_color(self) -> QColor:
        """The fault-line QColor for the current dominant direction."""
        from price_drift import FAVORABLE
        if self._drift is not None and self._drift.direction == FAVORABLE:
            return self.FAULT_VIOLET
        return self.FAULT_AMBER

    def _tremor_color(self, direction) -> QColor:
        """A per-row tick's QColor by ITS OWN direction (a row can move opposite
        to the card's net pole)."""
        from price_drift import FAVORABLE
        return self.FAULT_VIOLET if direction == FAVORABLE else self.FAULT_AMBER

    def _measure_drift(self, w, h, row_geom):
        """The SINGLE source of truth for the fault-line geometry (paint + hit
        rects + introspection all read this). Pure geometry — no painting.

        Builds, ONCE per (drift, size): the zig-zag crack QPainterPath in the
        x in [FAULT_X_MIN, FAULT_X_MAX] channel from y=FAULT_Y_MARGIN to
        y=h-FAULT_Y_MARGIN (kink count + amplitude scaled by magnitude, decision
        F), the epicenter diamond at the largest tremor's y, and a 2px tick rect
        per moved row (nudged inboard on a best row). `row_geom` is a list of
        (ident, y_top, is_best) for the CURRENT provider rows (so ticks land on
        the exact rows that moved). Returns a dict cached in self._drift_geom."""
        d = self._drift
        mag = max(0.0, min(1.0, d.magnitude))
        # decision F: kinks = clamp(3 + round(mag*6), 3, 9); amp = clamp(2+mag*5, 2, 7)
        kinks = max(3, min(9, 3 + round(mag * 6)))
        amp = max(2.0, min(7.0, 2.0 + mag * 5.0))

        y_top = self.FAULT_Y_MARGIN
        y_bot = max(y_top + 1.0, h - self.FAULT_Y_MARGIN)
        # x oscillates symmetrically inside [FAULT_X_MIN, FAULT_X_MAX] around the
        # channel mid; amplitude is clamped so it can never leave the channel.
        x_mid = (self.FAULT_X_MIN + self.FAULT_X_MAX) / 2.0
        half = (self.FAULT_X_MAX - self.FAULT_X_MIN) / 2.0
        a = min(amp, half)                       # never exceed the 7px channel

        n = kinks
        path = QPainterPath()
        pts = []
        for i in range(n + 1):
            t = i / float(n)
            y = y_top + t * (y_bot - y_top)
            # alternate left/right of the channel mid; a small phase so the
            # endpoints sit near the mid (a crack that starts/ends at the spine)
            x = x_mid + (a if (i % 2 == 0) else -a)
            if i == 0:
                x = x_mid
            elif i == n:
                x = x_mid
            pts.append(QPointF(x, y))
        path.moveTo(pts[0])
        for p in pts[1:]:
            path.lineTo(p)

        # Epicenter: the y of the LARGEST tremor's row if we can place it on a
        # known row; else the vertical centre of the crack. x sits on the spine
        # mid so the diamond reads as the crack's origin.
        epi_y = (y_top + y_bot) / 2.0
        if d.tremors and row_geom:
            top_ident = d.tremors[0].ident
            for ident, ry, _best in row_geom:
                if ident == top_ident:
                    epi_y = ry + self.ROW_H / 2.0
                    break
        epi = QPointF(x_mid, max(y_top, min(y_bot, epi_y)))

        # Per-row tremor ticks: a 2px vertical bar y+3..y+ROW_H-3 at PAD_X-4, or
        # PAD_X-2 on a best row (the coexistence nudge — clear the gold accent).
        # Keyed by ident so the row loop can paint each one in O(1) (layering it
        # over the best-row highlight) without re-deriving geometry.
        ticks = {}          # {ident: (QRectF, QColor)}
        tdir = {t.ident: t.direction for t in d.tremors}
        for ident, ry, is_best in row_geom:
            if ident not in d.moved_rows:
                continue
            tx = (self.PAD_X - 2) if is_best else (self.PAD_X - 4)
            rect = QRectF(tx, ry + 3, self.TICK_W, self.ROW_H - 6)
            ticks[ident] = (rect, self._tremor_color(tdir.get(ident, d.direction)))

        # the fault path's bounding rect (introspection / hit testing)
        bbox = path.boundingRect()
        return {
            "path": path, "bbox": bbox, "epi": epi, "ticks": ticks,
            "kinks": kinks, "amp": amp, "y_top": y_top, "y_bot": y_bot,
        }

    def _drift_row_geom(self, w, h):
        """Replicate the band-stack y math to get each provider row's top y +
        best flag BEFORE the row loop runs (so the crack's epicenter + the tick
        rects can be measured up-front). Pure — mirrors the paintEvent stack
        accumulation exactly. Returns [(ident, y_top, is_best)]."""
        if not self._endpoints or not self._endpoints.endpoints:
            return []
        y = self.PAD_Y + self.HEADER_H
        bands = 0
        if self._benchmark is not None:
            y += self.CREST_H; bands += 1
        if self._speed is not None:
            if bands:
                y += self.BAND_GAP
            y += self.SPEED_H; bands += 1
        if self._door is not None:
            if bands:
                y += self.BAND_GAP
            y += self.DOOR_H; bands += 1
        if bands:
            y += self.ROWS_GAP
        out = []
        for ep in self._endpoints.endpoints:
            out.append((self._ep_ident(ep), y, self._best is ep))
            y += self.ROW_H
        return out

    def _paint_fault_line(self, painter, w, h):
        """Paint the seismograph crack down the card's LEFT EDGE + the epicenter
        diamond + the fresh-Δ glyph, all clipped to the rounded BG path so the
        crack stays inside the corners (decision F). Per-row tremor TICKS are
        painted in the row loop (so they layer over the best-row highlight),
        reading the same cached geometry. Guarded by has_drift() at the call
        site — paints NOTHING when quiet (decision E). Records the clickable
        edge-band + tick hit rects into self._drift_hits."""
        d = self._drift
        row_geom = self._drift_row_geom(w, h)
        geom = self._measure_drift(w, h, row_geom)
        self._drift_geom = geom
        color = self._drift_color()

        # Hit rects: the whole left-edge band (0..PAD_X-2) PLUS each tick rect.
        self._drift_hits = [(QRectF(0, 0, self.PAD_X - 2, h), None)]
        for ident, (rect, _c) in geom["ticks"].items():
            self._drift_hits.append((QRectF(rect), ident))

        painter.save()
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        painter.setClipPath(bg)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # FRESH state breathes the crack's alpha on the shared shimmer (decision
        # E); static otherwise. Mutate the ONE preallocated QColor (allocation-
        # free, mirrors _pulse_alpha).
        if self._drift_fresh and self._shimmer_timer.isActive():
            a = int(150 + 80 * math.sin(self._shimmer * 2 * math.pi))   # ~70..230
        else:
            a = 230

        # (1) soft glow pass — 3px, lower alpha
        self._drift_alpha.setRgb(color.red(), color.green(), color.blue(),
                                 min(110, int(a * 0.45)))
        painter.setPen(QPen(self._drift_alpha, 3.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(geom["path"])
        # (2) crisp trace — 1.5px, high alpha
        self._drift_alpha.setRgb(color.red(), color.green(), color.blue(), a)
        painter.setPen(QPen(self._drift_alpha, 1.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(geom["path"])

        # epicenter diamond (filled) + a 1px ring at the largest tremor's y
        epi = geom["epi"]
        r = self.FAULT_EPI_R
        diamond = QPolygonF([
            QPointF(epi.x(), epi.y() - r), QPointF(epi.x() + r, epi.y()),
            QPointF(epi.x(), epi.y() + r), QPointF(epi.x() - r, epi.y()),
        ])
        self._drift_alpha.setRgb(color.red(), color.green(), color.blue(), a)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._drift_alpha))
        painter.drawPolygon(diamond)
        ring = QColor(color.red(), color.green(), color.blue(), min(160, a))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ring, 1.0))
        painter.drawEllipse(epi, r + 2.0, r + 2.0)
        painter.restore()

        # (3) FRESH-Δ glyph at top-left (3, PAD_Y+ascent) — only while unacked.
        if self._drift_fresh:
            painter.save()
            dfont = Fonts.tiny()
            dfm = QFontMetrics(dfont)
            self._drift_alpha.setRgb(color.red(), color.green(), color.blue(), a)
            painter.setPen(QPen(self._drift_alpha))
            painter.setFont(dfont)
            painter.drawText(QPointF(3, self.PAD_Y + dfm.ascent()), "Δ")  # Δ
            painter.restore()

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

    # ---- THE PULSE (#3 — the per-row 73h uptime cardiogram) ----

    def set_uptime(self, histories):
        """histories: {ep_ident: UptimeHistory}. Per-endpoint hourly uptime
        handed down by the dashboard. Builds the measure-once cardiogram geometry
        for every endpoint ONCE here (the paint hot path only strokes the cached
        objects — honors the GC-disabled / allocation-free-paint invariant), then
        repaints. NEVER calls _update_height: this glyph lives in the existing
        ROW_H row and adds no height (decision A — no reflow)."""
        self._uptime = dict(histories or {})
        self._pulse_cache = {}
        alive = False
        for ident, hist in self._uptime.items():
            if hist is None:
                continue
            observed = [v for v in hist.values if v is not None]
            # A flawless endpoint (no sub-99 hour over a full window) earns the
            # opt-in heartbeat (decision C). len>=72 guards a too-short window.
            if len(hist) >= 72 and hist.outage_hours == 0 and observed:
                alive = True
        self._uptime_alive = alive
        self._sync_shimmer()
        self.update()

    def has_uptime(self) -> bool:
        return bool(self._uptime)

    def _uptime_for_ep(self, ep):
        """The UptimeHistory for an endpoint row, or None."""
        return self._uptime.get(self._ep_ident(ep)) if self._uptime else None

    def _measure_pulse(self, glyph, hist):
        """The SINGLE source of truth for the cardiogram geometry (paint + hit
        rect + introspection all read this). Returns a dict, or None when there
        is not enough observed data to draw a cardiogram (caller falls back to
        the legacy %-chip). Pure geometry — no painting."""
        vals = hist.values
        n = len(vals)
        observed = [(i, v) for i, v in enumerate(vals) if v is not None]
        if n < 2 or len(observed) < 2:
            return None

        inner_left = glyph.left() + 1.0
        inner_right = glyph.right() - 1.0
        inner_w = inner_right - inner_left
        y = glyph.top()
        baseline_y = y + self.ROW_H * 0.5
        floor_y = y + self.ROW_H - self.PULSE_FLOOR_MARGIN
        down_span = floor_y - baseline_y
        span = max(1, n - 1)
        amp = self.PULSE_AMP_UP
        beat = self.PULSE_BEAT
        beat_len = len(beat)
        thresh = self.PULSE_DIP_THRESH
        denom = thresh - self.PULSE_DIP_FLOOR   # 99 - 31 = 68

        def x_of(i):
            return inner_left + i * (inner_w / span)

        def y_of(i, v):
            if v >= thresh:
                return baseline_y - amp * beat[i % beat_len]
            depth = (thresh - v) / denom
            depth = 0.0 if depth < 0.0 else 1.0 if depth > 1.0 else depth
            return baseline_y + depth * down_span

        # Build polylines split on None gaps; collect dip x's + the worst point.
        segments = []          # list[QPolygonF] — healthy/whole trace
        red_runs = []          # list[QPolygonF] — contiguous runs that hold a dip
        dip_xs = []
        cur = QPolygonF()
        run_has_dip = False
        for i, v in enumerate(vals):
            if v is None:
                if not cur.isEmpty():
                    segments.append(cur)
                    if run_has_dip:
                        red_runs.append(cur)
                cur = QPolygonF()
                run_has_dip = False
                continue
            px = x_of(i)
            py = y_of(i, v)
            cur.append(QPointF(px, py))
            if v < thresh:
                dip_xs.append((px, py, baseline_y))   # (x, trough_y, baseline_y)
                run_has_dip = True
        if not cur.isEmpty():
            segments.append(cur)
            if run_has_dip:
                red_runs.append(cur)

        worst_i, worst_v = min(observed, key=lambda iv: iv[1])
        worst_pt = QPointF(x_of(worst_i), y_of(worst_i, worst_v))
        has_outage = hist.outage_hours > 0
        avg = hist.average

        # Healthy-stroke base color: amber if the endpoint carries scars,
        # else green; then desaturate by rolling average so a chronically
        # wobbly endpoint reads subtly sick even with no single outage hour.
        base = QColor(Colors.YELLOW) if has_outage else QColor(Colors.GREEN)
        if avg is not None:
            t = (99.9 - avg) / 4.0
            t = 0.0 if t < 0.0 else 0.6 if t > 0.6 else t
            base = _lerp_color(base, QColor(Colors.TEXT_MUTED), t)

        return {
            "segments": segments,
            "red_runs": red_runs,
            "dip_xs": dip_xs,
            "worst_pt": worst_pt,
            "has_outage": has_outage,
            "avg": avg,
            "baseline_y": baseline_y,
            "base": base,
            "glyph": QRectF(glyph),
            "inner_left": inner_left,
            "inner_w": inner_w,
            "n": n,
        }

    def uptime_accent(self, ident) -> str:
        """GREEN for a clean endpoint, RED when its worst hour < 95% — handed to
        popup.set_accent() so the dossier border matches the verdict."""
        hist = self._uptime.get(ident) if self._uptime else None
        if hist is not None:
            worst = hist.worst
            if worst is not None and worst[1] < 95.0:
                return "#ff4757"
        return "#2ed573"

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
        # #5 THE THRESHOLD rides the SAME data (no new fetch): resolve the
        # cheapest door from the endpoints we just received. set_door re-measures
        # height itself if the band toggled, so this is safe after _update_height.
        self._resolve_door_from_endpoints()
        # #6 THE WATERLINE rides the SAME endpoints payload (the now-widened F1
        # pricing_extra): resolve + cache the per-row hidden-fee classes. Adds no
        # height, so this never re-measures.
        self.set_fees()
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

    def _pulse_at(self, pos):
        """(rect, ident) of the uptime cardiogram under `pos`, or (None, None)."""
        for rect, ident, _color in self._pulse_hits:
            if rect.contains(pos):
                return rect, ident
        return None, None

    def _waterline_at(self, pos):
        """(rect, ident) of the #6 price-cell waterline under `pos`, or
        (None, None). Only rows with a hidden-fee class or buoy are present."""
        for rect, ident, _color in self._waterline_hits:
            if rect.contains(pos):
                return rect, ident
        return None, None

    def _drift_at(self, pos):
        """The fault-line click rect under `pos` (the whole left-edge band PLUS
        each tremor tick), or None. A tick is checked FIRST so a click on a
        tremor returns that tick's tighter rect (a nicer popup anchor); the broad
        edge band is the fallback. Empty when quiet (no _drift_hits)."""
        if not self._drift_hits:
            return None
        band = None
        for rect, ident in self._drift_hits:
            if ident is None:
                band = rect            # the edge band (checked last as fallback)
                continue
            if rect.contains(pos):
                return rect
        if band is not None and band.contains(pos):
            return band
        return None

    def mouseMoveEvent(self, event):
        pos = event.position()
        icon_hover = self._icon_hit_rect.contains(pos)
        crest_hover = self._benchmark is not None and self._crest_hit_rect.contains(pos)
        speed_hover = self._speed is not None and self._speed_hit_rect.contains(pos)
        door_hover = self._door is not None and self._door_hit_rect.contains(pos)
        trend_hover = self._trend is not None and self._tape_hit_rect.contains(pos)
        drift_hover = self._drift_at(pos) is not None
        _, seal_ident = self._seal_at(pos)
        _, pulse_ident = self._pulse_at(pos)
        _, wl_ident = self._waterline_at(pos)
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
        if door_hover != self._door_hover:
            self._door_hover = door_hover
            changed = True
        if trend_hover != self._trend_hover:
            self._trend_hover = trend_hover
            changed = True
        if seal_ident != self._seal_hover_ident:
            self._seal_hover_ident = seal_ident
            changed = True
        if pulse_ident != self._pulse_hover_ident:
            self._pulse_hover_ident = pulse_ident
            changed = True
        # The waterline + the fault line are static (decision E — the crack only
        # breathes when fresh, on its own timer) — track the hovered price cell /
        # crack only to show the hand cursor; neither triggers a repaint.
        cursor_changed = (wl_ident != self._waterline_hover_ident
                          or drift_hover != self._drift_hover)
        self._waterline_hover_ident = wl_ident
        self._drift_hover = drift_hover
        if changed or cursor_changed:
            if (icon_hover or crest_hover or speed_hover or door_hover
                    or trend_hover or drift_hover
                    or seal_ident is not None or pulse_ident is not None
                    or wl_ident is not None):
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.unsetCursor()
            if changed:
                self.update()

    def leaveEvent(self, event):
        if (self._icon_hover or self._crest_hover or self._speed_hover
                or self._door_hover or self._trend_hover or self._drift_hover
                or self._seal_hover_ident is not None
                or self._pulse_hover_ident is not None
                or self._waterline_hover_ident is not None):
            self._icon_hover = False
            self._crest_hover = False
            self._speed_hover = False
            self._door_hover = False
            self._trend_hover = False
            self._drift_hover = False
            self._seal_hover_ident = None
            self._pulse_hover_ident = None
            self._waterline_hover_ident = None
            self.unsetCursor()
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        seal_rect, seal_ident = self._seal_at(pos)
        # The pulse + waterline hit-tests are LAST so they can never steal a
        # seal/band click — though their columns never geometrically overlap them.
        pulse_rect, pulse_ident = self._pulse_at(pos)
        wl_rect, wl_ident = self._waterline_at(pos)
        if self._icon_hit_rect.contains(pos):
            global_pos = self.mapToGlobal(self._icon_hit_rect.center().toPoint())
            self.info_clicked.emit(self.model_id, QPointF(global_pos))
        elif self._trend is not None and self._tape_hit_rect.contains(pos):
            global_pos = self.mapToGlobal(self._tape_hit_rect.center().toPoint())
            self.trend_clicked.emit(self.model_id, QPointF(global_pos))
        elif self._benchmark is not None and self._crest_hit_rect.contains(pos):
            global_pos = self.mapToGlobal(self._crest_hit_rect.center().toPoint())
            self.arena_clicked.emit(self.model_id, QPointF(global_pos))
        elif self._speed is not None and self._speed_hit_rect.contains(pos):
            global_pos = self.mapToGlobal(self._speed_hit_rect.center().toPoint())
            self.speed_clicked.emit(self.model_id, QPointF(global_pos))
        elif self._door is not None and self._door_hit_rect.contains(pos):
            global_pos = self.mapToGlobal(self._door_hit_rect.center().toPoint())
            self.door_clicked.emit(self.model_id, QPointF(global_pos))
        elif (drift_rect := self._drift_at(pos)) is not None:
            # #8 THE FAULT LINE — the left-edge crack band OR a per-row tremor
            # tick. Tested BEFORE the seal so a tremor tick (which nudges to
            # PAD_X-2, grazing the seal column) always opens the Seismograph
            # rather than the row's Custody dossier.
            global_pos = self.mapToGlobal(drift_rect.center().toPoint())
            self.drift_clicked.emit(self.model_id, QPointF(global_pos))
        elif seal_ident is not None:
            global_pos = self.mapToGlobal(seal_rect.center().toPoint())
            self.trust_clicked.emit(self.model_id, seal_ident, QPointF(global_pos))
        elif pulse_ident is not None:
            global_pos = self.mapToGlobal(pulse_rect.center().toPoint())
            self.uptime_clicked.emit(self.model_id, pulse_ident, QPointF(global_pos))
        elif wl_ident is not None:
            global_pos = self.mapToGlobal(wl_rect.center().toPoint())
            self.fees_clicked.emit(self.model_id, wl_ident, QPointF(global_pos))

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

        # #8 THE FAULT LINE — the price-drift seismograph crack, painted right
        # after the BG path (clipped to it) and BEFORE the header so it reads as
        # a fracture in the card's own casing. Paints NOTHING when quiet (no
        # drift / magnitude 0) — the silent-degrade contract (decision E). Per-
        # row tremor ticks are layered in the row loop below; this also measures
        # + caches the geometry both halves read.
        self._drift_hits = []
        if self.has_drift():
            self._paint_fault_line(painter, w, h)

        # === Header layout: name (elided) · inline ★ chip · [#7 TAPE] · (i) icon ===
        # Reserve space right-to-left so the name never overlaps the chip, the
        # Tape, or the icon, no matter how long the name is. #7 (decision C): the
        # ★ best-chip RELOCATED inline (right of the name); THE TAPE now owns the
        # far-right gutter the chip used to occupy.
        GAP_CHIP_TO_ICON = self.GAP_CHIP_TO_ICON
        GAP_NAME_TO_CHIP = self.GAP_NAME_TO_CHIP

        # Icon at the far right
        icon_right = w - self.PAD_X
        icon_x = icon_right - self.ICON_VISIBLE
        icon_y = self.PAD_Y + (self.HEADER_H - self.ICON_VISIBLE) / 2
        icon_pad = (self.ICON_HIT - self.ICON_VISIBLE) / 2
        self._icon_hit_rect = QRectF(
            icon_x - icon_pad, icon_y - icon_pad,
            self.ICON_HIT, self.ICON_HIT,
        )

        name_fm = QFontMetrics(Fonts.subheading())
        baseline = self.PAD_Y + (self.HEADER_H + name_fm.ascent() - name_fm.descent()) / 2.0

        # --- #7 THE TAPE geometry FIRST (right-to-left off the icon) so the name
        #     boundary can reserve its gutter. Only when we have a trend; else the
        #     gutter is free and the name/chip group reclaims it. ---
        tape_left = None
        tape_cx = tape_cy = 0.0
        tape_pill = QRectF()
        if self._trend is not None:
            tf = Fonts.tiny(); tf.setBold(True)
            tfm = QFontMetrics(tf)
            stamp_w = tfm.horizontalAdvance(self._trend_stamp)
            pill_w = self.TAPE_TRACE_W + self.TAPE_TRACE_GAP + stamp_w + self.TAPE_HPAD
            tape_right = icon_x - GAP_CHIP_TO_ICON
            tape_left = tape_right - pill_w
            tape_cy = baseline - name_fm.ascent() * 0.32     # optical center of header text
            tape_pill = QRectF(tape_left, tape_cy - self.TAPE_H / 2.0, pill_w, self.TAPE_H)
            self._tape_hit_rect = QRectF(tape_left - 3, tape_cy - 9, pill_w + 6, 18)
        else:
            self._tape_hit_rect = QRectF()

        # The right boundary of the elastic name+chip run: the Tape's left edge
        # (minus the gap) when present, otherwise the old chip reservation.
        group_right = ((tape_left - GAP_NAME_TO_CHIP) if tape_left is not None
                       else (icon_x - GAP_CHIP_TO_ICON))

        # Chip width via real font metrics (now drawn INLINE, right of the name).
        chip_text = f"★ {self._best.provider_name}" if self._best is not None else ""
        chip_font = Fonts.tiny()
        chip_fm = QFontMetrics(chip_font)
        chip_w = chip_fm.horizontalAdvance(chip_text) if chip_text else 0

        # Name fills from PAD_X, leaving room for the inline chip + the gutter
        # reservation. Keep the existing max(40,…) floor so a long name elides
        # rather than colliding with the Tape. On a very narrow card the floored
        # name + the inline chip can't BOTH fit before the gutter — in that case
        # DROP the chip (it's a nice-to-have marker; the name + Tape are the
        # priority), mirroring the speed band dropping its tier word when tight.
        name = self._display_model_name()
        if chip_text:
            budget = int(group_right - chip_w - GAP_NAME_TO_CHIP - self.PAD_X)
            if budget < 40:
                chip_text = ""          # no room for name-floor AND chip → drop it
                name_max_w = max(40, int(group_right - self.PAD_X))
            else:
                name_max_w = budget
        else:
            name_max_w = max(40, int(group_right - self.PAD_X))
        elided_name = name_fm.elidedText(name, Qt.TextElideMode.ElideRight, name_max_w)
        name_right = self.PAD_X + name_fm.horizontalAdvance(elided_name)

        painter.setPen(Colors.TEXT_PRIMARY)
        painter.setFont(Fonts.subheading())
        painter.drawText(QPointF(self.PAD_X, baseline), elided_name)

        # Inline ★ chip — immediately right of the (elided) name, same baseline.
        if chip_text:
            chip_left = name_right + GAP_NAME_TO_CHIP
            painter.setPen(Colors.CYAN)
            painter.setFont(chip_font)
            painter.drawText(QPointF(float(chip_left), baseline), chip_text)

        # --- #7 THE TAPE cartouche (after the header text) ---
        if self._trend is not None:
            self._paint_tape(painter, tape_pill, tape_cy)

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

        # #5 THE THRESHOLD — painted LAST in the stack so the door reads as the
        # "exit" below the model's standing (Arena rank → Speed → Door).
        if self._door is not None:
            if drew_band:
                y += self.BAND_GAP
            self._paint_door(painter, y)
            y += self.DOOR_H
            drew_band = True
        else:
            self._door_hit_rect = QRectF()

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
        self._pulse_hits = []
        self._waterline_hits = []          # #6 clickable rows (depth>0 or buoy)
        self._waterline_buoy_rects = {}    # #6 recorded buoy rings (test introspection)
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

            # #8 THE FAULT LINE — per-row tremor tick on the EXACT providers that
            # moved (the spatial read a toast can't do). Layered AFTER the gold
            # accent so the +2px-inboard nudge (decision F) reads clearly on a
            # best row. Reads the geometry measured once in _paint_fault_line.
            if self._drift_geom is not None:
                tick = self._drift_geom["ticks"].get(self._ep_ident(ep))
                if tick is not None:
                    trect, tcolor = tick
                    painter.fillRect(trect, QBrush(tcolor))

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

            # THE PULSE (#3): the 73h cardiogram in the uptime column. Falls
            # back to the legacy %-chip when there's no (or too little) history
            # so a failed/absent fetch never blanks the row.
            up_glyph = QRectF(up_right - UPTIME_W, y, UPTIME_W, self.ROW_H)
            ident = self._ep_ident(ep)
            hist = self._uptime.get(ident) if self._uptime else None
            measured = None
            if hist is not None:
                measured = self._pulse_cache.get(ident)
                if measured is None:
                    measured = self._measure_pulse(up_glyph, hist)
                    self._pulse_cache[ident] = measured
            if measured is not None:
                hover = (self._pulse_hover_ident == ident)
                self._paint_pulse(painter, measured, hover)
                accent = self.uptime_accent(ident)
                self._pulse_hits.append((QRectF(up_glyph), ident, accent))
            else:
                up_text, up_color = self._uptime_chip(ep.uptime)
                painter.setPen(up_color)
                painter.setFont(Fonts.mono_small())
                painter.drawText(
                    up_glyph,
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

            # #6 THE WATERLINE — the hidden-cost iceberg painted in the price
            # cell's sub-baseline slack, right under the digits we just drew.
            # Zero width, zero height, no column shift (decision F).
            self._paint_waterline(painter, ep, y, price_right, PRICE_W,
                                  price_text, is_best)

            y += self.ROW_H

        painter.end()

    # ---- #6 THE WATERLINE rendering (allocation-light: fills cached classes) ----

    def _paint_waterline(self, painter, ep, y, price_right, PRICE_W, price_text,
                         is_best):
        """Paint ONE row's iceberg waterline under its price + (far-left) the
        implicit-caching buoy. Reads the per-ident classes/depth/buoy resolved
        in set_fees. A CLEAN row (no classes, no buoy) draws NOTHING and records
        no hit rect — silent honest degrade (decision D). Gate off → no-op."""
        if not self._show_fees:
            return
        ident = self._ep_ident(ep)
        classes = self._waterline_fee_classes.get(ident, frozenset())
        depth = self._waterline_depth.get(ident, 0.0)
        buoy = self._waterline_buoy.get(ident, False)
        if not classes and not buoy:
            return                          # clean row → nothing, not clickable

        from api_client import (WATERLINE_SURFACE, WATERLINE_ABYSS,
                                WATERLINE_EDGE, FEE_CLASS_ORDER)
        painter.save()
        # (1) MEASURE the price cell — the SAME VCenter math the price text used.
        fm = QFontMetrics(Fonts.mono_small())
        pw = fm.horizontalAdvance(price_text)
        tx = price_right - pw               # text left edge (right-aligned)
        # price_baseline = where the digits' baseline sits (AlignVCenter):
        price_baseline = y + (self.ROW_H + fm.ascent() - fm.descent()) / 2.0
        # Strip sits 2px BELOW the baseline, but is clamped into the row: never
        # past the bottom edge, and — decision F — never ABOVE the baseline (so
        # the sea-level line can't ride up into the digits even on a tight font).
        floor_clamp = y + self.ROW_H - 0.5  # never paint past the row's bottom
        strip_top = min(price_baseline + 2.0, floor_clamp - 3.0)
        strip_top = max(strip_top, price_baseline)

        if classes:
            # (2) WATER FILL: the calm steel-teal sea the price floats on.
            wl = QRectF(tx, strip_top, pw, 3.0)
            surf = QColor(WATERLINE_SURFACE); surf.setAlpha(90)
            sea = QPainterPath(); sea.addRoundedRect(wl, 1.5, 1.5)
            painter.fillPath(sea, QBrush(surf))
            # the SUBMERGED portion grows from the LEFT (deep end), pw*depth wide.
            sub_w = max(0.0, pw * depth)
            if sub_w > 0:
                abyss = QColor(WATERLINE_ABYSS); abyss.setAlpha(160)
                sub = QPainterPath()
                sub.addRoundedRect(QRectF(tx, wl.top(), sub_w, 3.0), 1.5, 1.5)
                painter.fillPath(sub, QBrush(abyss))
            # (3) WATERLINE EDGE: a 1px pale-aqua sea-level line on top.
            edge = QColor(WATERLINE_EDGE); edge.setAlpha(200)
            painter.setPen(QPen(edge, 1))
            painter.drawLine(QPointF(tx, wl.top()), QPointF(tx + pw, wl.top()))
            # (4) FEE TICKS: one 2px notch per present class, hanging below the
            # strip — short ticks = submerged mass. Evenly spread across pw.
            tick = QColor(WATERLINE_ABYSS); tick.setAlpha(150)
            painter.setPen(QPen(tick, 2))
            present = [c for c in FEE_CLASS_ORDER if c in classes]
            nticks = len(present)
            if nticks:
                tick_top = wl.bottom()
                tick_h = 3.0
                tick_bot = min(tick_top + tick_h, floor_clamp)
                # spread the ticks across the strip width (left → right)
                for k in range(nticks):
                    frac = (k + 0.5) / nticks
                    tx_k = tx + frac * pw
                    painter.drawLine(QPointF(tx_k, tick_top),
                                     QPointF(tx_k, tick_bot))

        # (5) BUOY: a hollow abyss-teal ring at the far-left margin marking
        # implicit-caching support. Default x=PAD_X-4 centered on the row; on a
        # BEST row it shifts +4px DOWN to clear the gold accent bar (decision G).
        if buoy:
            d = 5.0
            cx = self.PAD_X - 4 + d / 2.0
            cy = y + self.ROW_H / 2.0
            if is_best:
                cy += 4.0                   # clear the gold best-accent bar
            ring = QColor(WATERLINE_ABYSS); ring.setAlpha(200)
            painter.setPen(QPen(ring, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            buoy_rect = QRectF(cx - d / 2.0, cy - d / 2.0, d, d)
            painter.drawEllipse(buoy_rect)
            self._waterline_buoy_rects[ident] = QRectF(buoy_rect)

        painter.restore()
        # (6) HIT RECT — only when there's something to decode (depth>0 or buoy).
        # The whole price cell is the click target (mirrors _pulse_hits).
        self._waterline_hits.append(
            (QRectF(price_right - PRICE_W, y, PRICE_W, self.ROW_H),
             ident, WATERLINE_SURFACE))

    # ---- THE PULSE rendering (allocation-free: strokes cached geometry) ----

    def _paint_pulse(self, painter, m, hover):
        """Stroke ONE row's cached cardiogram. No allocation beyond reusing the
        single preallocated self._pulse_alpha QColor for the heartbeat lift."""
        baseline_y = m["baseline_y"]
        inner_left = m["glyph"].left() + 1.0
        inner_right = m["glyph"].right() - 1.0
        base = m["base"]
        has_outage = m["has_outage"]
        # On hover, brighten the whole trace to full alpha + a faint halo.
        stroke = QColor(base)
        if hover:
            stroke.setAlpha(255)

        painter.save()
        # Update the live introspection fields to the row currently painted so
        # the deterministic test can read the last (single-row) card's geometry.
        self._pulse_glyph_rect = m["glyph"]
        self._pulse_baseline_y = baseline_y
        self._pulse_worst_pt = m["worst_pt"]
        self._pulse_dip_xs = [x for (x, _ty, _by) in m["dip_xs"]]
        self._pulse_has_outage = has_outage
        self._pulse_avg = m["avg"]

        # 1. faint isoelectric baseline guide — a "monitor" feel.
        guide = QColor(base)
        guide.setAlpha(46)
        painter.setPen(QPen(guide, 1))
        painter.drawLine(QPointF(inner_left, baseline_y),
                         QPointF(inner_right, baseline_y))

        # 2. healthy/whole trace (round caps so the calm pulse looks alive).
        pen = QPen(stroke, 1.4, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for seg in m["segments"]:
            painter.drawPolyline(seg)

        # 3. re-stroke any run that holds a dip in RED, on top.
        red = QColor(Colors.RED)
        if m["red_runs"]:
            painter.setPen(QPen(red, 1.9, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for run in m["red_runs"]:
                painter.drawPolyline(run)

        # 4. scar ticks: a 1px vertical drop from baseline to each trough
        #    (guarantees a >=1px mark even when the hour is sub-pixel narrow).
        if m["dip_xs"]:
            painter.setPen(QPen(red, 1))
            for (x, ty, by) in m["dip_xs"]:
                painter.drawLine(QPointF(x, by), QPointF(x, ty))

        # 5. worst dot: the single point the eye lands on.
        if has_outage and m["worst_pt"] is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(red))
            painter.drawEllipse(m["worst_pt"], 1.7, 1.7)

        # 6. flawless heartbeat (opt-in, decision C): a tiny brightness ease on
        #    the systole blip nearest the moving phase — only when this row is
        #    flawless AND the shared shimmer is running. Mutates ONE preallocated
        #    QColor (no new geometry / allocation).
        if (not has_outage and self._uptime_alive
                and self._shimmer_timer.isActive() and m["segments"]):
            seg = m["segments"][0]
            n_pts = seg.count()
            if n_pts:
                idx = int(self._shimmer * n_pts) % n_pts
                p = seg.at(idx)
                a = int(150 + 105 * math.sin(self._shimmer * 2 * math.pi))
                self._pulse_alpha.setRgb(base.red(), base.green(), base.blue(),
                                         max(0, min(255, a)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(self._pulse_alpha))
                painter.drawEllipse(p, 1.5, 1.5)

        painter.restore()

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

    def _paint_tape(self, painter, pill, cy):
        """#7 THE TAPE — the torn-ticker momentum cartouche. A notched-paper pill
        holding a 3-point SLOPED trace (steeper = stronger, NOT a binary arrow) +
        a stamped delta, in the amber(riser)/violet(faller)/grey(flat) lane. The
        trace's slope encodes magnitude geometrically; explosive risers get a
        ghost-double trace + a live-wire shimmer (the 'off the chart' tell, vs a
        meaningless huge %). `pill` is the cartouche QRectF; `cy` its center y."""
        line_col, _fill = self._trend_lane()
        hover = self._trend_hover
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # --- pill backing (lane@26, hover 42) + stroke (lane@70, hover 130) ---
        bg = QColor(line_col); bg.setAlpha(42 if hover else 26)
        bpath = QPainterPath(); bpath.addRoundedRect(pill, 4, 4)
        painter.fillPath(bpath, QBrush(bg))
        bd = QColor(line_col); bd.setAlpha(130 if hover else 70)
        painter.setPen(QPen(bd, 1)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(bpath)

        # --- TWO torn-paper notch triangles on the pill's LEFT edge (the wire
        #     tell that it's ripped-off ticker tape, beating a plain arrow) ---
        nt = float(self.TAPE_NOTCH)
        lx = pill.left()
        notch = QColor(Colors.BG_CARD)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QBrush(notch))
        for ny in (pill.top() + pill.height() * 0.30, pill.top() + pill.height() * 0.70):
            tri = QPolygonF([QPointF(lx, ny - nt), QPointF(lx + nt, ny), QPointF(lx, ny + nt)])
            painter.drawPolygon(tri)

        # --- THE TRACE: position the cached UNIT shape into the left slot. y in
        #     the unit shape grows downward (riser already descends left→right). ---
        slot_l = pill.left() + self.TAPE_HPAD / 2.0
        trace_h = self.TAPE_H - 6.0
        top = cy - trace_h / 2.0
        pts = [QPointF(slot_l + p.x() * self.TAPE_TRACE_W, top + p.y() * trace_h)
               for p in self._tape_trace_pts]

        def stroke_trace(color, width):
            tp = QPainterPath(); tp.moveTo(pts[0])
            for q in pts[1:]:
                tp.lineTo(q)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(tp)

        # explosive: a faint ghost-trace offset +1.5px ('off the chart'); its
        # alpha pulses on the shared shimmer timer (live wire), allocation-free.
        if self._trend_explosive:
            ghost = QColor(line_col)
            a = int(70 + 70 * math.sin(self._shimmer * 2 * math.pi))
            ghost.setAlpha(max(0, min(255, a)))
            painter.save()
            painter.translate(1.5, 1.5)
            stroke_trace(ghost, 1.6)
            painter.restore()

        stroke_trace(line_col, 1.6)

        # head dot on the rightmost (latest) tick; breathes for explosive risers.
        head = pts[-1]
        r = self.TAPE_DOT_R
        if self._trend_explosive:
            r = self.TAPE_DOT_R + 0.6 * math.sin(self._shimmer * 2 * math.pi)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QBrush(line_col))
        painter.drawEllipse(head, r, r)

        # --- THE STAMP: tiny bold, right of the trace, vertically centered ---
        tf = Fonts.tiny(); tf.setBold(True)
        painter.setFont(tf)
        painter.setPen(line_col)
        stamp_x = slot_l + self.TAPE_TRACE_W + self.TAPE_TRACE_GAP
        painter.drawText(QRectF(stamp_x, pill.top(), pill.right() - stamp_x, pill.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self._trend_stamp)
        painter.restore()

    def _paint_door(self, painter, y):
        """#5 THE THRESHOLD — a hand-painted perspective DOOR-LEAF band. A stone
        jamb on the shared left rail marks the CURRENT (best) provider; a fake-
        perspective leaf swings open toward the CHEAPER destination; the saving %
        is engraved on the lintel ('SAVE NN% · {provider}'). Brass-amber normally;
        it turns EMERALD and spills light through the gap (the literal GREEN DOOR,
        '+ FASTER') only when the cheaper provider is ALSO faster. Mirrors
        _paint_speed's band idiom + font-metric-driven fallback cascade; one
        QFontMetrics(Fonts.tiny() bold) measure pass feeds paint AND the test."""
        d = self._door
        band = QRectF(self.PAD_X - 2, y,
                      self.width() - 2 * (self.PAD_X - 2), self.DOOR_H)
        self._door_hit_rect = band
        ACCENT = QColor(self._door_accent)
        green = self._door_green

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1) BAND BACKING (same idiom as the crest/speed bands, but amber/emerald)
        bg = QColor(ACCENT); bg.setAlpha(34 if self._door_hover else 18)
        bpath = QPainterPath(); bpath.addRoundedRect(band, 8, 8)
        painter.fillPath(bpath, QBrush(bg))
        bd = QColor(ACCENT); bd.setAlpha(105 if self._door_hover else 45)
        painter.setPen(QPen(bd, 1)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(bpath)

        # --- one measure pass for the lintel text (feeds paint + the test) ---
        f = Fonts.tiny(); f.setBold(True)
        fm = QFontMetrics(f)
        save_txt = f"SAVE {d.save_pct}%"
        faster_txt = " + FASTER" if green else ""
        headline = save_txt + faster_txt
        tail = f" · {d.cheaper_name}"            # destination, elided/dropped first
        chev_w = 12
        chev_x = band.right() - 8 - chev_w
        self._door_chev_x = chev_x
        glyph_w = 7.0 if green else 0.0          # the emerald lightning glyph slot

        # 2) THE JAMB (left rail post = the source/best provider). On the SAME
        #    emblem column as the Arena hexagon / Speed bolt.
        jamb_cx = self._icon_col_cx()            # 21
        post_x = jamb_cx - 1.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(QRectF(post_x, band.top() + 5, 2.0,
                                band.height() - 10), QColor(0x5a, 0x55, 0x66))
        # a small square hinge nub at the post's vertical mid, on its right side
        nub_cy = band.center().y()
        painter.fillRect(QRectF(post_x + 2.0, nub_cy - 1.5, 3.0, 3.0),
                         QColor(0x6e, 0x68, 0x7d))

        # 3) THE DOOR LEAF — a perspective trapezoid hinged at the jamb. Hinge
        #    edge full-height on the post; swinging edge SHORTER + offset right.
        hinge_x = jamb_cx + 1.0                  # 22
        swing_x = jamb_cx + 19.0                 # 40 — confined to x in [22,40]
        leaf = QPolygonF([
            QPointF(hinge_x, band.top() + 4),         # top-hinge
            QPointF(swing_x, band.top() + 8),         # top-swing (perspective)
            QPointF(swing_x, band.bottom() - 3),      # bottom-swing
            QPointF(hinge_x, band.bottom() - 4),      # bottom-hinge
        ])
        self._door_leaf_poly = leaf
        # GREEN-DOOR light spill: a faint emerald glow in the OPEN gap, behind
        # the leaf, so the rare cheaper-AND-faster case is unmissable.
        if green:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0x34, 0xd2, 0x7e, 70)))
            painter.drawEllipse(QPointF(swing_x + 5.0, band.center().y()),
                                7.0, band.height() / 2.0 - 3.0)
        leaf_grad = QLinearGradient(hinge_x, band.top(), swing_x, band.top())
        if green:
            leaf_grad.setColorAt(0.0, QColor(0x1f, 0x7a, 0x4d, 150))
            leaf_grad.setColorAt(1.0, QColor(0x34, 0xd2, 0x7e, 150))
        else:
            leaf_grad.setColorAt(0.0, QColor(0xb0, 0x7a, 0x2e, 150))
            leaf_grad.setColorAt(1.0, QColor(0xe0, 0xa1, 0x3a, 150))
        painter.setBrush(QBrush(leaf_grad))
        outline = QColor(ACCENT); outline.setAlpha(180)
        painter.setPen(QPen(outline, 1))
        painter.drawPolygon(leaf)
        # one vertical panel-groove line inside the leaf
        groove = QColor(ACCENT); groove.setAlpha(90)
        painter.setPen(QPen(groove, 1))
        gx = hinge_x + (swing_x - hinge_x) * 0.5
        painter.drawLine(QPointF(gx, band.top() + 7), QPointF(gx, band.bottom() - 6))
        # a tiny round 'knob' near the swinging edge
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(ACCENT).lighter(150)))
        painter.drawEllipse(QPointF(swing_x - 3.0, band.center().y()), 1.5, 1.5)

        # 4) THE LINTEL TEXT — text_left computed (clears the open leaf), with the
        #    fallback cascade: drop the ' · provider' tail first, then elide the
        #    headline (mirror _paint_speed).
        startX = swing_x + 6.0                    # jamb_cx + leaf_swing + 6 (~46)
        MIN_LANE = 44
        show_tail = True

        def total_w(with_tail):
            return (glyph_w + fm.horizontalAdvance(headline)
                    + (fm.horizontalAdvance(tail) if with_tail else 0))

        text_left = chev_x - self.CHEV_GAP - total_w(show_tail)
        if text_left - startX < MIN_LANE:         # tight → drop the destination tail
            show_tail = False
            text_left = chev_x - self.CHEV_GAP - total_w(show_tail)
        head_draw, elided = headline, False
        if text_left - startX < MIN_LANE:         # still tight → elide the headline
            avail = max(0, int(chev_x - self.CHEV_GAP - glyph_w - startX))
            head_draw = fm.elidedText(headline, Qt.TextElideMode.ElideRight, avail)
            text_left = chev_x - self.CHEV_GAP - glyph_w - fm.horizontalAdvance(head_draw)
            elided = True
        text_left = max(startX, text_left)
        self._door_text_left = text_left

        painter.setFont(f)
        tx = text_left
        # GREEN-DOOR: a 5px hand-painted 2-stroke emerald lightning glyph
        if green and not elided:
            painter.setPen(QPen(QColor(0x34, 0xd2, 0x7e), 1.4,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            gcy = band.center().y()
            painter.drawLine(QPointF(tx + 3.5, gcy - 5), QPointF(tx + 0.5, gcy + 0.5))
            painter.drawLine(QPointF(tx + 0.5, gcy + 0.5), QPointF(tx + 4.0, gcy + 5))
            tx += glyph_w

        if elided:
            painter.setPen(QColor(0xe8, 0xc2, 0x7a))
            painter.drawText(QRectF(tx, band.top(), chev_x - tx, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             head_draw)
        else:
            # 'SAVE NN%' (+ ' + FASTER') in warm gold, then the dim destination tail
            painter.setPen(QColor(0xe8, 0xc2, 0x7a))
            hw = fm.horizontalAdvance(headline)
            painter.drawText(QRectF(tx, band.top(), hw + 2, band.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             headline)
            tx += hw
            if show_tail:
                painter.setPen(Colors.TEXT_MUTED)
                painter.drawText(QRectF(tx, band.top(),
                                        max(0.0, chev_x - self.CHEV_GAP - tx),
                                        band.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 tail)

        # 5) RIGHT EDGE chevron (click affordance), identical to _paint_speed.
        painter.setFont(Fonts.body())
        painter.setPen(ACCENT if self._door_hover else QColor(120, 120, 150))
        painter.drawText(QRectF(chev_x, band.top() - 1, chev_w, band.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter, "›")
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

    # ---- #7 THE TAPE dossier (week-over-week request-momentum read) ----

    def trend_html(self, ident=None) -> str:
        """The Tape dossier: a week-over-week headline ('+50% this week — you
        picked a RISER'), the raw change read, a HONEST 2-point 'last 7d'
        momentum ramp embedded as a data-URI <img> (we have ONE delta — NOT a
        fabricated series), and a plain-English verdict that foreshadows #8 (the
        derank watch). `ident` is accepted to match the other *_html signatures;
        the tape is per-model, not per-row."""
        ch = self._trend
        if ch is None:
            return ""
        line_col, _ = self._trend_lane()
        accent = _safe_color(line_col.name(), "#9b8ccb")
        name = html.escape(self._display_model_name())
        stamp = html.escape(self._trend_stamp)
        explosive = self._trend_explosive
        sign = self._tape_slope_sign

        # Headline word + the precise change read.
        if explosive:
            word, wcol = "NEW ENTRANT", accent
        elif sign > 0:
            word, wcol = "RISER", accent
        elif sign < 0:
            word, wcol = "FALLER", accent
        else:
            word, wcol = "FLAT", "#a0a0c8"

        # Honest precise read: a literal % for the normal band, a multiplier for
        # the explosive band (matches the card stamp's honest-magnitude rule).
        if explosive:
            precise = f"{ch + 1:.1f}× last week's request volume"
        elif sign == 0:
            precise = f"{ch:+.1%} week-over-week (essentially flat)"
        else:
            precise = f"{ch:+.1%} week-over-week requests"

        out = [f"<div style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</div>"]
        out.append(
            f"<div style='color:{wcol};font-size:9.5pt;font-weight:bold;margin-bottom:2px;'>"
            f"&#9656; {stamp} this week &nbsp;·&nbsp; you picked a {word}</div>")
        out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
                   "Week-over-week request momentum · live from openrouter.ai</div>")

        # The honest 2-point ramp, embedded as a data-URI image.
        try:
            pm = TrendRampWidget(ch, line_col).render_pixmap()
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            pm.save(buf, "PNG")
            buf.close()
            b64 = bytes(ba.toBase64()).decode("ascii")
            out.append(
                f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
                f"width='{TrendRampWidget.STRIP_W}' height='{TrendRampWidget.STRIP_H}'></div>")
            out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
                       "&#9664; last week &nbsp;&nbsp;&nbsp; now &#9654;</div>")
        except Exception:
            log.debug("trend ramp render failed", exc_info=True)

        out.append(
            f"<div style='color:#c8c8e0;font-size:9pt;margin-bottom:6px;'>"
            f"<span style='color:{accent};font-weight:bold;'>{html.escape(precise)}</span></div>")

        verdict = self._trend_verdict(ch, sign, explosive)
        out.append(
            f"<div style='margin-top:2px;color:{accent};font-size:8.5pt;"
            f"font-style:italic;'>{html.escape(verdict)}</div>")
        out.append("<div style='margin-top:6px;color:#64648c;font-size:8pt;'>"
                   "Share of week-over-week request growth · refreshed every 20 min</div>")
        return "".join(out)

    def _trend_verdict(self, ch, sign, explosive) -> str:
        """Plain-English read that foreshadows #8 (the price/derank watch)."""
        if explosive:
            return "New entrant — treat as rocket-or-noise: a fresh listing can spike then fade."
        if sign > 0:
            return "Riser: more people are routing here than last week."
        if sign < 0:
            if ch <= -0.9:
                return "Cratering: traffic has all but drained — watch for a derank."
            return "Faller: traffic draining vs last week — watch for a derank."
        return "Holding steady: request volume is roughly flat week-over-week."

    # ---- #5 THE THRESHOLD dossier (the FROM→THROUGH comparison + honesty line) --

    def door_html(self, ident=None) -> str:
        """The 'cheapest door' dossier: a FROM (current/best provider) row and a
        THROUGH THE DOOR (cheaper provider) row with its -Z% price cut, a width-
        scaled delta bar, the absolute $/Mtok gap, and — so the band NEVER
        overstates — an HONESTY LINE ('cheaper but 14% slower') whenever the door
        isn't the green (cheaper-AND-faster) case. `ident` is accepted to match
        the other *_html signatures; the door is per-model, not per-row."""
        d = self._door
        if d is None:
            return ""
        accent = self.door_accent()
        name = html.escape(self._display_model_name())
        from_name = html.escape(d.from_name)
        to_name = html.escape(d.cheaper_name)

        def money(mtok):
            return f"${mtok:.3f}/Mtok"

        def lat(ms):
            if ms is None:
                return "—"
            return f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f} ms"

        def tput(t):
            return f"{t:.0f} t/s" if t is not None else "—"

        # width-scaled delta bar: the saving as a proportion of the FROM price.
        bar_n = max(2, min(24, int(round(d.save_pct / 100 * 24))))
        bar = (f"<span style='background-color:{accent};color:{accent};'>"
               f"{'&nbsp;' * bar_n}</span>")

        out = [f"<div style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</div>"]
        head = (f"&#9656; SAVE {d.save_pct}% + FASTER" if d.green
                else f"&#9656; SAVE {d.save_pct}%")
        out.append(
            f"<div style='color:{accent};font-size:9pt;font-weight:bold;margin-bottom:2px;'>"
            f"{head} &nbsp;·&nbsp; through {to_name}</div>")
        out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:8px;'>"
                   "Cheapest priced provider vs the current best · live from "
                   "openrouter.ai</div>")

        def row(label, label_col, prov, mtok, ms, t, extra=""):
            return (
                f"<tr>"
                f"<td style='padding:2px 12px 2px 0;color:{label_col};font-weight:bold;white-space:nowrap;'>{label}</td>"
                f"<td style='padding:2px 10px 2px 0;color:#f0f0ff;white-space:nowrap;'>{prov}</td>"
                f"<td style='padding:2px 10px 2px 0;color:#f0f0ff;font-weight:bold;white-space:nowrap;' align='right'>{money(mtok)}{extra}</td>"
                f"<td style='padding:2px 10px 2px 0;color:#a0a0c8;white-space:nowrap;' align='right'>{lat(ms)}</td>"
                f"<td style='padding:2px 0;color:#a0a0c8;white-space:nowrap;' align='right'>{tput(t)}</td>"
                f"</tr>")

        cut = (f" <span style='color:{accent};'>(-{d.save_pct}%)</span>")
        out.append(
            "<table cellspacing='0' style='border-spacing:0;margin-bottom:6px;'>"
            + "<tr><td></td><td></td>"
              "<td style='color:#64648c;font-size:8pt;' align='right'>$/Mtok</td>"
              "<td style='color:#64648c;font-size:8pt;' align='right'>first&nbsp;token</td>"
              "<td style='color:#64648c;font-size:8pt;' align='right'>stream</td></tr>"
            + row("FROM", "#a0a0c8", from_name, d.from_mtok, d.from_latency, d.from_throughput)
            + row("THROUGH THE DOOR", accent, to_name, d.to_mtok, d.to_latency,
                  d.to_throughput, extra=cut)
            + "</table>")

        gap = d.from_mtok - d.to_mtok
        out.append(
            f"<div style='margin-bottom:6px;'>{bar}"
            f"<span style='color:#c8c8e0;font-size:8.5pt;'>&nbsp;&nbsp;"
            f"saves <b style='color:{accent};'>${gap:.3f}/Mtok</b> on input"
            f"</span></div>")

        # THE HONESTY LINE — the band never lies. Green ⇒ celebrate; otherwise
        # state the trade-off (slower / unknown speed) plainly.
        honesty = self._door_honesty(d)
        out.append(
            f"<div style='margin-top:6px;color:{accent if d.green else '#d0a060'};"
            f"font-size:8.5pt;font-style:italic;'>{html.escape(honesty)}</div>")
        out.append("<div style='margin-top:6px;color:#64648c;font-size:8pt;'>"
                   "Door swings to the cheapest priced provider · turns emerald "
                   "only when it is ALSO faster · refreshed with endpoints</div>")
        return "".join(out)

    def _door_honesty(self, d) -> str:
        """The plain-English trade-off so the band never overstates."""
        if d.green:
            return f"The green door: {d.save_pct}% cheaper AND faster to stream."
        ld = d.latency_delta_pct
        # throughput trade-off (the green-door axis) takes precedence in wording
        if (d.from_throughput is not None and d.to_throughput is not None
                and d.to_throughput < d.from_throughput):
            slower = round(100 * (d.from_throughput - d.to_throughput) / d.from_throughput) \
                if d.from_throughput > 0 else None
            if slower:
                return f"Cheaper, but streams {slower}% slower."
        if ld is not None and ld > 0:
            return f"Cheaper, but {ld}% slower to first token."
        if d.to_throughput is None and d.to_latency is None:
            return "Cheaper — speed for this provider isn't reported yet."
        return "Cheaper, with comparable speed."

    # ---- #6 THE WATERLINE dossier (the hidden-cost decode) ----

    def fees_html(self, ident) -> str:
        """The 'WHAT THE STICKER PRICE HIDES' fine-print dossier for one provider
        row: the listed prompt/completion price (the visible tip), then one line
        per hidden fee actually present (value in $/Mtok or $/call + a ratio-vs-
        prompt phrase), and an implicit-caching ON/OFF footer. Empty string for a
        clean row (no hidden fees AND no implicit caching) — nothing to decode.
        Every API-sourced string is HTML-escaped (mirrors door_html/speed_html)."""
        ep = self._ep_by_ident(ident)
        if ep is None:
            return ""
        from api_client import hidden_fee_classes, HIDDEN_MAX, WATERLINE_SURFACE
        classes = hidden_fee_classes(ep)
        buoy = bool(ep.supports_implicit_caching)
        if not classes and not buoy:
            return ""                       # clean row — the strip drew nothing

        accent = WATERLINE_SURFACE
        name = html.escape(self._display_model_name())
        prov = html.escape(self._provider_label(ep))
        pm = ep.price_per_mtok_prompt       # $/Mtok prompt
        cm = ep.price_per_mtok_completion   # $/Mtok completion

        def mtok(per_token):                # a $/token fee → $/Mtok
            return per_token * 1_000_000

        def mtok_fmt(v):                     # a $/Mtok value → trimmed string
            if v >= 1:
                return f"{v:,.2f}"
            s = f"{v:.4f}".rstrip("0").rstrip(".")
            return s if s else "0"

        def call_fmt(v):                     # a $/call value → trimmed string
            s = f"{v:.4f}".rstrip("0").rstrip(".")
            return s if s else "0"

        def ratio_to(v_mtok, base_mtok, base_label):
            if not base_mtok or base_mtok <= 0:
                return ""
            r = v_mtok / base_mtok
            if r >= 1:
                rs = f"{r:.2f}".rstrip("0").rstrip(".")
            else:
                rs = f"{r:.2g}"
            return f"{rs}× {base_label}"

        out = [f"<div style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</div>"]
        out.append(
            f"<div style='color:{accent};font-size:9pt;font-weight:bold;"
            f"margin-bottom:2px;'>&#9656; WHAT THE STICKER PRICE HIDES "
            f"&nbsp;·&nbsp; {prov}</div>")
        # the visible tip
        out.append(
            f"<div style='color:#c8c8e0;font-size:8.5pt;margin-bottom:2px;'>"
            f"Listed: <b style='color:#f0f0ff;'>${mtok_fmt(pm)}</b> in / "
            f"<b style='color:#f0f0ff;'>${mtok_fmt(cm)}</b> out per Mtok "
            f"<span style='color:#64648c;'>— that's the tip.</span></div>")
        out.append(
            f"<div style='color:#64648c;font-size:8pt;margin-bottom:8px;'>"
            f"{len(classes)} hidden fee class{'' if len(classes) == 1 else 'es'} "
            f"of {HIDDEN_MAX} below the waterline · live from openrouter.ai</div>")

        # One row per hidden fee actually present (value>0). Each row: name,
        # value, ratio-vs-prompt/completion phrase.
        def row(label, value_str, note):
            return (
                f"<tr>"
                f"<td style='padding:2px 12px 2px 0;color:{accent};font-weight:bold;"
                f"white-space:nowrap;'>{label}</td>"
                f"<td style='padding:2px 10px 2px 0;color:#f0f0ff;font-weight:bold;"
                f"white-space:nowrap;' align='right'>{value_str}</td>"
                f"<td style='padding:2px 0;color:#a0a0c8;white-space:nowrap;'>{note}</td>"
                f"</tr>")

        rows = []
        # cache read / write — each its own line (the class collapses for the
        # strip, but the dossier shows both member fees when present).
        if ep.has_fee("input_cache_read"):
            v = mtok(ep.fee("input_cache_read"))
            rows.append(row("cache read", f"${mtok_fmt(v)}/Mtok",
                            ratio_to(v, pm, "prompt")))
        if ep.has_fee("input_cache_write"):
            v = mtok(ep.fee("input_cache_write"))
            rows.append(row("cache write", f"${mtok_fmt(v)}/Mtok",
                            ratio_to(v, pm, "prompt")))
        if ep.has_fee("web_search"):
            # web_search is $/call, NOT $/token — show it as-is.
            v = ep.fee("web_search")
            rows.append(row("web search", f"${call_fmt(v)}/call",
                            "billed per search request"))
        if ep.has_fee("internal_reasoning"):
            v = mtok(ep.fee("internal_reasoning"))
            note = ratio_to(v, cm, "completion")
            note = (note + " · billed on tokens you never see") if note else \
                "billed on tokens you never see"
            rows.append(row("reasoning", f"${mtok_fmt(v)}/Mtok", note))
        for key, lbl in (("image", "image"), ("audio", "audio"),
                         ("input_audio_cache", "audio cache")):
            if ep.has_fee(key):
                v = mtok(ep.fee(key))
                rows.append(row(lbl, f"${mtok_fmt(v)}/Mtok",
                                ratio_to(v, pm, "prompt")))

        if rows:
            out.append(
                "<table cellspacing='0' style='border-spacing:0;margin-bottom:6px;'>"
                "<tr><td></td>"
                "<td style='color:#64648c;font-size:8pt;' align='right'>rate</td>"
                "<td style='color:#64648c;font-size:8pt;padding-left:0;'>&nbsp;vs listed</td></tr>"
                + "".join(rows)
                + "</table>")

        # implicit-caching footer (decision C — buoy/footer only when truthy).
        if buoy:
            out.append(
                f"<div style='margin-top:4px;color:{accent};font-size:8.5pt;'>"
                f"&#9711; implicit caching <b>ON</b> "
                f"<span style='color:#a0a0c8;'>— repeated prompts are auto-cached "
                f"at the cache-read rate, no code change.</span></div>")
        else:
            out.append(
                "<div style='margin-top:4px;color:#64648c;font-size:8.5pt;'>"
                "&#9711; implicit caching <b>OFF</b> "
                "— you pay full prompt rate every call unless you cache explicitly.</div>")
        out.append(
            "<div style='margin-top:6px;color:#64648c;font-size:8pt;'>"
            "The waterline shows how much of the true cost sits below the listed "
            "price · deeper = more hidden fee classes</div>")
        return "".join(out)

    # ---- THE PULSE dossier (#3 — the painted 73h "Vitals" strip + verdict) ----

    def _longest_clean_streak(self, vals) -> int:
        """Max run of consecutive >=99% hours (a None breaks the run)."""
        best = cur = 0
        for v in vals:
            if v is not None and v >= 99.0:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    def _uptime_provider_name(self, ident) -> str:
        ep = self._ep_by_ident(ident)
        if ep is not None:
            return self._provider_label(ep)
        return ident

    def uptime_html(self, ident) -> str:
        """The Vitals dossier: a verdict, the PAINTED 73h strip (embedded as a
        data-URI <img> so the popup stays a single QLabel), a rhythm stat table,
        and either a DEEPEST-DIP callout (outage) or a FLAWLESS banner (clean)."""
        hist = self._uptime.get(ident) if self._uptime else None
        if hist is None or len([v for v in hist.values if v is not None]) < 2:
            return ""
        name = html.escape(self._uptime_provider_name(ident))
        vals = hist.values
        avg = hist.average
        latest = hist.latest
        worst = hist.worst
        outage = hist.outage_hours
        n = len(hist)
        flawless = (outage == 0 and n >= 72)

        out = [f"<div style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</div>"]

        # Verdict line, colored by health.
        if flawless:
            out.append(
                "<div style='color:#2ed573;font-size:9.5pt;font-weight:bold;"
                f"margin-bottom:2px;'>&#9829; FLAWLESS &nbsp;·&nbsp; {n}/{n} hours steady</div>")
        elif outage == 0:
            out.append(
                "<div style='color:#2ed573;font-size:9.5pt;font-weight:bold;"
                "margin-bottom:2px;'>CLEAN &nbsp;·&nbsp; no outage hours observed</div>")
        else:
            wv = worst[1] if worst else 0.0
            wd = html.escape(str(worst[0])) if worst else "—"
            out.append(
                "<div style='color:#ff4757;font-size:9.5pt;font-weight:bold;"
                f"margin-bottom:2px;'>{outage} OUTAGE HOUR{'' if outage == 1 else 'S'} "
                f"&nbsp;·&nbsp; worst {wv:.0f}% on {wd}</div>")
        out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
                   "73-hour vitals · live from openrouter.ai</div>")

        # The painted 73-bar strip, embedded as a data-URI image.
        try:
            pm = UptimeStripWidget(hist).render_pixmap()
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            pm.save(buf, "PNG")
            buf.close()
            b64 = bytes(ba.toBase64()).decode("ascii")
            out.append(
                f"<div style='margin-bottom:6px;'><img src='data:image/png;base64,{b64}' "
                f"width='{UptimeStripWidget.STRIP_W}' height='{UptimeStripWidget.STRIP_H}'></div>")
            out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
                       "&#9664; 73h ago &nbsp;&nbsp;&nbsp; now &#9654;</div>")
        except Exception:
            log.debug("uptime strip render failed", exc_info=True)

        # Rhythm-strip stat table (speed_html's gauge_row idiom).
        def stat(label, value, color="#f0f0ff"):
            return (f"<tr>"
                    f"<td style='padding:2px 14px 2px 0;color:#a0a0c8;white-space:nowrap;'>{label}</td>"
                    f"<td align='right' style='padding:2px 0;color:{color};font-weight:bold;"
                    f"white-space:nowrap;'>{value}</td></tr>")

        def pct(v):
            return f"{v:.2f}%" if v is not None else "—"

        wcell = "—"
        if worst is not None:
            wcol = "#ff4757" if worst[1] < 95.0 else "#f0f0ff"
            wcell = (f"<span style='color:{wcol};'>{worst[1]:.1f}% "
                     f"<span style='color:#64648c;'>· {html.escape(str(worst[0]))}</span></span>")
        streak = self._longest_clean_streak(vals)
        rows = (
            stat("Latest", pct(latest))
            + stat("73h Average", pct(avg))
            + f"<tr><td style='padding:2px 14px 2px 0;color:#a0a0c8;white-space:nowrap;'>Worst Hour</td>"
              f"<td align='right' style='padding:2px 0;white-space:nowrap;'>{wcell}</td></tr>"
            + stat("Outage Hours", str(outage), "#ff4757" if outage else "#2ed573")
            + stat("Longest Clean Streak", f"{streak} h")
        )
        out.append(f"<table cellspacing='0' style='font-size:9pt;border-spacing:0;"
                   f"margin-bottom:6px;'>{rows}</table>")

        # Deepest-dip callout (outage) OR flawless-streak banner (clean).
        if outage > 0 and worst is not None:
            mins = round((100.0 - worst[1]) / 100.0 * 60)
            out.append(
                "<div style='background-color:#2a1416;border-left:3px solid #ff4757;"
                "padding:5px 8px;margin-bottom:4px;'>"
                "<span style='color:#ff6b78;font-weight:bold;font-size:8.5pt;'>DEEPEST DIP</span> "
                f"<span style='color:#f0f0ff;font-size:9pt;'>&nbsp;{worst[1]:.0f}% "
                f"<span style='color:#a0a0c8;'>· {html.escape(str(worst[0]))}</span></span><br>"
                f"<span style='color:#a0a0c8;font-size:8pt;'>endpoint was down ~{mins} min "
                "of that hour</span></div>")
        else:
            out.append(
                "<div style='background-color:#13241a;border-left:3px solid #2ed573;"
                "padding:5px 8px;margin-bottom:4px;'>"
                "<span style='color:#2ed573;font-weight:bold;font-size:8.5pt;'>FLAWLESS STREAK</span> "
                f"<span style='color:#c8c8e0;font-size:8.5pt;'>&nbsp;{streak} consecutive "
                "healthy hours</span></div>")

        out.append("<div style='margin-top:4px;color:#64648c;font-size:8pt;'>"
                   "live from openrouter.ai · 73 hourly samples · refreshed every ~20 min</div>")
        return "".join(out)

    # ---- #8 THE FAULT LINE dossier (the Seismograph read-out) ----

    @staticmethod
    def _drift_ago(ts) -> str:
        """A coarse 'since {relative time}' phrase for a unix ts (the baseline /
        last-quiet reading). 'just now' under a minute; else m/h/d."""
        if not ts:
            return "the last reading"
        import time as _t
        dt = max(0.0, _t.time() - ts)
        if dt < 60:
            return "just now"
        if dt < 3600:
            return f"{int(dt // 60)} min ago"
        if dt < 86400:
            return f"{int(dt // 3600)} h ago"
        return f"{int(dt // 86400)} d ago"

    @staticmethod
    def _drift_ts_str(ts) -> str:
        if not ts:
            return "—"
        import time as _t
        return _t.strftime("%b %d, %H:%M", _t.localtime(ts))

    def drift_html(self, ident=None) -> str:
        """The SEISMOGRAPH dossier: a header ('since {relative time}'), one line
        per tremor (price up/down · cheaper appeared · deranked) with a tiny
        inline width-scaled magnitude bar colored by ITS direction, and a 'last
        quiet reading {ts}' footer. `ident` is accepted to match the other
        *_html signatures; the drift is per-model. Empty string when quiet.

        Every API-sourced provider name is HTML-escaped (mirrors door_html)."""
        from price_drift import (FAVORABLE, KIND_PRICE_UP, KIND_PRICE_DOWN,
                                  KIND_CHEAPER, KIND_DERANK)
        d = self._drift
        if d is None or d.magnitude <= 0.0:
            return ""
        net_accent = self.drift_accent()
        amber = self.FAULT_AMBER.name()
        violet = self.FAULT_VIOLET.name()
        name = html.escape(self._display_model_name())

        out = [f"<div style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</div>"]
        verdict = ("PRICES SHIFTED — WATCH" if d.direction != FAVORABLE
                   else "FAVORABLE DRIFT")
        out.append(
            f"<div style='color:{net_accent};font-size:9.5pt;font-weight:bold;"
            f"margin-bottom:2px;'>&#9651; SEISMOGRAPH &nbsp;·&nbsp; {verdict}</div>")
        out.append(
            f"<div style='color:#64648c;font-size:8pt;margin-bottom:8px;'>"
            f"since {html.escape(self._drift_ago(d.baseline_ts))} · "
            f"{len(d.moved_rows)} provider{'' if len(d.moved_rows) == 1 else 's'} moved "
            f"· live from openrouter.ai</div>")

        def money(per_tok):
            mtok = per_tok * 1_000_000
            return f"${mtok:.3f}/Mtok"

        def bar(mag, col):
            n = max(2, min(24, int(round(mag * 24))))
            return (f"<span style='background-color:{col};color:{col};'>"
                    f"{'&nbsp;' * n}</span>")

        rows = []
        for t in d.tremors:
            col = violet if t.direction == FAVORABLE else amber
            pname = html.escape(t.name or t.ident)
            if t.kind == KIND_PRICE_UP:
                glyph = "&#9650;"   # ▲
                desc = (f"{money(t.old)} &#8594; {money(t.new)} "
                        f"<span style='color:{col};'>+{abs(t.rel) * 100:.0f}%</span>")
            elif t.kind == KIND_PRICE_DOWN:
                glyph = "&#9660;"   # ▼
                desc = (f"{money(t.old)} &#8594; {money(t.new)} "
                        f"<span style='color:{col};'>-{abs(t.rel) * 100:.0f}%</span>")
            elif t.kind == KIND_CHEAPER:
                glyph = "&#9660;"   # ▼
                desc = (f"now <b style='color:{col};'>CHEAPEST</b> at {money(t.new)} "
                        f"<span style='color:#64648c;'>(under {money(t.old)})</span>")
            elif t.kind == KIND_DERANK:
                glyph = "&#9888;"   # ⚠
                desc = f"<b style='color:{col};'>DERANKED</b> by the router"
            else:
                glyph = "&#9651;"
                desc = ""
            rows.append(
                f"<tr>"
                f"<td style='padding:3px 8px 3px 0;color:{col};font-weight:bold;"
                f"white-space:nowrap;vertical-align:top;'>{glyph}</td>"
                f"<td style='padding:3px 10px 3px 0;color:#f0f0ff;font-weight:bold;"
                f"white-space:nowrap;vertical-align:top;'>{pname}</td>"
                f"<td style='padding:3px 0;color:#c8c8e0;font-size:8.5pt;"
                f"white-space:nowrap;vertical-align:top;'>{desc}"
                f"<br>{bar(t.magnitude, col)}</td>"
                f"</tr>")
        out.append("<table cellspacing='0' style='border-spacing:0;font-size:9pt;"
                   "margin-bottom:6px;'>" + "".join(rows) + "</table>")

        out.append(
            f"<div style='margin-top:4px;color:#64648c;font-size:8pt;'>"
            f"Last quiet reading: {html.escape(self._drift_ts_str(d.baseline_ts))} "
            f"· crack persists until you open this · refreshed with endpoints</div>")
        return "".join(out)

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


# ---------------------------------------------------------------------------
#  THE SPECTRUM (#9) — stacked gradient-area ground-truth spend ribbon
# ---------------------------------------------------------------------------
# The canonical Spend unlock copy (shared verbatim across all six set_locked
# paths; each tailors only the trailing verb). A small painted padlock precedes
# it. NEVER fake numbers when locked — TEXT_MUTED only.
SPEND_UNLOCK_BASE = "Add a management key at openrouter.ai to unlock"


def _paint_padlock(painter: QPainter, cx: float, cy: float, size: float,
                   color: QColor):
    """A tiny QPainter-drawn padlock (rounded-rect body + arc shackle),
    centered at (cx, cy). Shared by the Spend zone's locked states."""
    painter.save()
    pen = QPen(color, max(1.0, size * 0.12))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    body_w = size * 0.78
    body_h = size * 0.6
    body = QRectF(cx - body_w / 2, cy - body_h / 2 + size * 0.16, body_w, body_h)
    painter.drawRoundedRect(body, size * 0.12, size * 0.12)
    # shackle: an upward arc sitting on top of the body
    sh_w = body_w * 0.62
    sh = QRectF(cx - sh_w / 2, body.top() - body_h * 0.62, sh_w, body_h * 0.9)
    painter.drawArc(sh, 0, 180 * 16)
    painter.restore()


class SpendSpectrum(QWidget):
    """THE SPECTRUM (#9): a hand-painted stacked gradient-area chart of daily
    ground-truth spend — every model a colored band (time on X, model
    composition stacked on Y) — anchored top-right by a count-up range TOTAL and
    below by a per-model legend-spine. A spike day glows as a clickable column.

    Echoes TimelineChart's gradient-area fill idiom + ArcGauge's single held
    QPropertyAnimation count-up + the measure-then-paint geometry contract (one
    _measure() feeds both paint and setFixedHeight so nothing clips). The reveal
    is one-time, gated on a signature compare so 15-min polls don't re-animate.

    Click entry points (signals wired now, consumed by #10/#11):
      band_clicked(model_id, global_anchor)  -> #10 receipt popup
      spike_clicked(t0_iso, t1_iso)           -> #11 autopsy (a tap = single bucket)
      spike_selected(t0_iso, t1_iso)          -> #11 autopsy (a drag = lassoed window)

    #11 THE AUTOPSY lasso (decision A): press-drag-release across the CHART BODY
    selects a spend window; on release a drag wider than DRAG_MIN_PX emits
    spike_selected(t0,t1) (the lassoed bucket range), while a sub-threshold tap
    falls through to the existing single-bucket spike_clicked / band_clicked
    hit-testing — so a normal click on a legend row (#10) is NEVER swallowed.
    The selection band + dim-outside veil + a cursor $-readout (running Σ of the
    touched buckets' stacked totals, from already-cached data — NO network) paint
    INSIDE #9's already-sized chart body (zero added width/height). Locked ->
    the lasso is disabled (mousePress does nothing).
    """

    band_clicked = Signal(str, QPointF)
    spike_clicked = Signal(str, str)
    spike_selected = Signal(str, str)

    # A drag narrower than this (px) is a TAP, not a lasso — the threshold that
    # stops a normal legend/band click being swallowed by the autopsy (decision A).
    DRAG_MIN_PX = 6.0

    # -- geometry constants (the measure pass derives everything from these) --
    PAD_X = 14
    PAD_TOP = 26
    PAD_BOTTOM = 18
    CHART_H = 130
    SWATCH = 10
    BAND_TOP_STROKE = 1.4
    MAX_LEGEND_ROWS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = None          # SpendSpectrumData | None
        self._locked = False
        self._signature = None     # cheap reveal-gate signature
        self._reveal = 1.0         # 0..1 grow factor (animated once)
        # Cached geometry from the measure pass (rebuilt in set_data/resize):
        self._band_polys = []      # list[(model_id, QPolygonF)] bottom→top
        self._legend_rects = []    # list[(model_id, QRectF)]
        self._spike_rect = None    # QRectF | None (the clickable spike column)
        self._legend_block_h = 0
        self._hover_model = None
        # -- #11 lasso state (set by the press/move/release handlers) --
        self._drag_x0 = None       # float | None: the press x while dragging
        self._drag_x = None        # float | None: the live cursor x while dragging
        self._selection = None     # tuple[float,float] | None: (x_left,x_right) px band

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

        # ONE held animation (ArcGauge idiom) — never per-frame alloc.
        self._anim = QPropertyAnimation(self, b"reveal")
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._measure()  # establish an initial fixed height (locked-ish chrome)
        theme_controller.changed.connect(self._on_theme_changed)

    # -- the reveal Property (distinct name; not a QWidget builtin) --
    def get_reveal(self):
        return self._reveal

    def set_reveal(self, v):
        self._reveal = float(v)
        self.update()

    reveal = Property(float, get_reveal, set_reveal)

    def _on_theme_changed(self):
        # Accent may have changed -> re-resolve band colors + repaint.
        self._build_geometry()
        self.update()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def set_data(self, data):
        """data: a SpendSpectrumData (board.spectrum) or None.

        None with no prior data => caller should use set_locked(); here we treat
        None defensively as 'keep last good'."""
        if data is None:
            return
        self._locked = False
        self._data = data
        sig = self._compute_signature(data)
        first_or_changed = (sig != self._signature)
        self._signature = sig
        self._measure()
        self._build_geometry()
        # One-time reveal: animate only on first populated render or when the
        # range/model-set signature changes (so a 15-min identical poll is
        # silent). Honor the app-wide animations flag.
        if first_or_changed and not data.is_empty:
            self._start_reveal()
        else:
            self._reveal = 1.0
        self.update()

    def set_locked(self):
        """No management key: paint full chrome + a faint ghost silhouette +
        the padlock + unlock line + greyed legend placeholders. Zero fake $."""
        self._locked = True
        self._data = None
        self._signature = None
        self._reveal = 1.0
        self._measure()
        self._band_polys = []
        self._legend_rects = []
        self._spike_rect = None
        self.update()

    # ------------------------------------------------------------------
    #  Reveal animation
    # ------------------------------------------------------------------
    def _start_reveal(self):
        try:
            import anim
            on = anim.ANIMATIONS_ON
        except Exception:
            on = True
        self._anim.stop()
        if not on:
            self._reveal = 1.0
            return
        self._reveal = 0.0
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    @staticmethod
    def _compute_signature(data):
        # Range + model set + per-model totals rounded — cheap and stable.
        return (
            data.granularity,
            data.buckets,
            tuple((m.model_id, round(m.total_usage, 4)) for m in data.models),
        )

    # ------------------------------------------------------------------
    #  Measure pass (drives BOTH paint and setFixedHeight — no clipping)
    # ------------------------------------------------------------------
    def _legend_row_h(self) -> int:
        return QFontMetrics(Fonts.body()).height() + 6

    def _legend_rows_count(self) -> int:
        if self._locked or self._data is None:
            return 3  # greyed placeholder rows
        n = len(self._data.models)
        if n == 0:
            return 1  # the "$0.00 / no spend" still leaves a tidy lane
        if n > self.MAX_LEGEND_ROWS:
            return self.MAX_LEGEND_ROWS + 1  # +1 for the "+N more" row
        return n

    def _measure(self):
        f_hero = Fonts.mono_large()
        f_tiny = Fonts.tiny()
        header_h = QFontMetrics(f_hero).height() + QFontMetrics(f_tiny).height()
        legend_row_h = self._legend_row_h()
        self._legend_block_h = legend_row_h * self._legend_rows_count()
        savings_strip_h = QFontMetrics(f_tiny).height() + 8  # RESERVED for #12
        total_h = (self.PAD_TOP + header_h + 8 + self.CHART_H + 10
                   + self._legend_block_h + savings_strip_h + self.PAD_BOTTOM)
        self._header_h = header_h
        self._legend_row_h_cached = legend_row_h
        self._savings_strip_h = savings_strip_h
        self.setFixedHeight(int(total_h))

    # ------------------------------------------------------------------
    #  Geometry build (cache band polygons + legend rects + spike rect)
    #  Runs in set_data/resize — NOT the paint hot path.
    # ------------------------------------------------------------------
    def _chart_geom(self):
        w = max(1, self.width())
        chart_left = self.PAD_X
        chart_w = w - 2 * self.PAD_X
        chart_top = self.PAD_TOP + self._header_h + 8
        chart_h = self.CHART_H
        return chart_left, chart_top, chart_w, chart_h

    def _chart_body_rect(self) -> QRectF:
        cl, ct, cw, ch = self._chart_geom()
        return QRectF(cl, ct, cw, ch)

    # -- the x->bucket inverse of #9's bucket->x map (the lasso's spine) --------
    def _x_to_bucket_index(self, x: float) -> int:
        """Map a chart-body x to the nearest bucket index [0, n-1]. The inverse of
        the build_geometry map xs[i] = chart_left + chart_w*i/(n-1) (and the
        single-bucket centered case). Clamped; returns -1 when there are no
        buckets."""
        data = self._data
        if data is None or not data.buckets:
            return -1
        n = len(data.buckets)
        cl, _, cw, _ = self._chart_geom()
        if n == 1:
            return 0
        frac = (x - cl) / cw if cw > 0 else 0.0
        idx = int(round(frac * (n - 1)))
        return max(0, min(n - 1, idx))

    def _selection_bucket_range(self):
        """The (i0, i1) inclusive bucket index span the current drag covers, or
        None. i0<=i1; built from _drag_x0/_drag_x snapped to nearest buckets."""
        if self._drag_x0 is None or self._drag_x is None:
            return None
        i0 = self._x_to_bucket_index(min(self._drag_x0, self._drag_x))
        i1 = self._x_to_bucket_index(max(self._drag_x0, self._drag_x))
        if i0 < 0 or i1 < 0:
            return None
        return (min(i0, i1), max(i0, i1))

    def _selection_window_iso(self):
        """The (t0_iso, t1_iso) bucket labels for the current drag span, or None.
        t1 is the LABEL of the last touched bucket (the worker ceils it to the
        hour) so a single-bucket drag still yields a real 1-bucket window."""
        rng = self._selection_bucket_range()
        if rng is None:
            return None
        i0, i1 = rng
        buckets = self._data.buckets
        return (buckets[i0], buckets[i1])

    def _selection_sum(self) -> float:
        """Running Σ of the stacked totals of the buckets the drag touches (from
        already-cached matrix data — NO network). Drives the cursor $-readout."""
        rng = self._selection_bucket_range()
        if rng is None or self._data is None:
            return 0.0
        i0, i1 = rng
        total = 0.0
        for m in self._data.models:
            row = self._data.matrix.get(m.model_id, [])
            for i in range(i0, i1 + 1):
                if 0 <= i < len(row):
                    total += row[i]
        return total

    def _build_geometry(self):
        self._band_polys = []
        self._legend_rects = []
        self._spike_rect = None
        data = self._data
        if data is None or data.is_empty:
            return
        chart_left, chart_top, chart_w, chart_h = self._chart_geom()
        n = len(data.buckets)
        if n == 0:
            return

        # x of each bucket center; a single bucket becomes a fat centered column.
        if n == 1:
            xs = [chart_left + chart_w / 2.0]
            col_w = chart_w * 0.55
        else:
            xs = [chart_left + chart_w * i / (n - 1) for i in range(n)]
            col_w = chart_w / (n - 1)

        chart_bottom = chart_top + chart_h
        # Per-bucket stacked totals define the y-scale (peak == full chart_h).
        bucket_totals = [
            sum(data.matrix[m.model_id][i] for m in data.models)
            for i in range(n)
        ]
        peak = max(bucket_totals) if bucket_totals else 0.0
        if peak <= 0:
            return
        usable_h = chart_h - 4  # tiny headroom so the top band doesn't clip

        def y_for(cum):
            return chart_bottom - usable_h * (cum / peak)

        # Build bands bottom→top (heaviest model first = rank 0 = floor band).
        # Cumulative bottom edge per bucket, ascending the stack.
        cum_bottom = [0.0] * n
        for m in data.models:
            row = data.matrix[m.model_id]
            top_pts = []   # left→right across the cumulative TOP edge
            bot_pts = []   # the cumulative BOTTOM edge (to walk back)
            for i in range(n):
                cb = cum_bottom[i]
                ct = cb + row[i]
                top_pts.append(QPointF(xs[i], y_for(ct)))
                bot_pts.append(QPointF(xs[i], y_for(cb)))
                cum_bottom[i] = ct
            if n == 1:
                # widen the single column into a real rectangle
                hx = col_w / 2.0
                poly = QPolygonF([
                    QPointF(xs[0] - hx, top_pts[0].y()),
                    QPointF(xs[0] + hx, top_pts[0].y()),
                    QPointF(xs[0] + hx, bot_pts[0].y()),
                    QPointF(xs[0] - hx, bot_pts[0].y()),
                ])
            else:
                poly = QPolygonF(top_pts + list(reversed(bot_pts)))
            self._band_polys.append((m.model_id, poly))

        # Spike column rect (the clickable autopsy entry).
        si = data.spike_index
        if 0 <= si < n:
            sx = xs[si]
            half = col_w / 2.0
            self._spike_rect = QRectF(sx - half, chart_top, max(8.0, col_w),
                                      chart_h)

        # Legend rows (top-aligned under the chart + 10px gap).
        legend_top = chart_bottom + 10
        row_h = self._legend_row_h_cached
        w = max(1, self.width())
        rows = min(len(data.models), self.MAX_LEGEND_ROWS)
        for idx in range(rows):
            r = QRectF(self.PAD_X, legend_top + idx * row_h,
                       w - 2 * self.PAD_X, row_h)
            self._legend_rects.append((data.models[idx].model_id, r))

    def resizeEvent(self, event):
        self._build_geometry()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    #  Paint (allocation-light: strokes cached polygons + measured rects)
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        # 1) ROUNDED BG (TimelineChart idiom)
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        painter.fillPath(bg, QBrush(Colors.BG_CARD))
        painter.setPen(QPen(Colors.BORDER, 1))
        painter.drawPath(bg)

        accent = theme_controller.accent()
        chart_left, chart_top, chart_w, chart_h = self._chart_geom()
        chart_bottom = chart_top + chart_h

        # 2) HEADER ROW — left range label, right hero total + ground-truth tag
        f_hero = Fonts.mono_large()
        f_tiny = Fonts.tiny()
        hero_h = QFontMetrics(f_hero).height()
        painter.setPen(Colors.TEXT_SECONDARY)
        painter.setFont(Fonts.label())
        label = "SPEND"
        if self._data is not None:
            label = "SPEND · " + self._data_range_label().upper()
        elif self._locked:
            label = "SPEND · LAST 7 DAYS"
        painter.drawText(QRectF(self.PAD_X, 4, chart_w, 18),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         label)

        hero_top = self.PAD_TOP - 4
        if self._locked:
            # a small lock glyph where the hero $ would be (no "$0.00")
            _paint_padlock(painter, w - self.PAD_X - 9, hero_top + hero_h / 2,
                           16, Colors.TEXT_MUTED)
        else:
            total = self._data.total if self._data is not None else 0.0
            shown = total * self._reveal
            hero_text = f"${shown:,.2f}"
            painter.setPen(Colors.TEXT_PRIMARY)
            painter.setFont(f_hero)
            hero_rect = QRectF(w / 2.0, hero_top, w / 2.0 - self.PAD_X, hero_h)
            painter.drawText(hero_rect,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             hero_text)
            # "ground truth" tag beneath, right-aligned
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(f_tiny)
            tag = "ground truth"
            if self._data is not None and self._data.truncated:
                tag = "ground truth · truncated"
            tag_rect = QRectF(w / 2.0, hero_top + hero_h,
                              w / 2.0 - self.PAD_X, QFontMetrics(f_tiny).height())
            painter.drawText(tag_rect,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, tag)
            # 2px accent underline under the hero total
            hw = QFontMetrics(f_hero).horizontalAdvance(hero_text)
            ux1 = (w - self.PAD_X) - hw
            uy = hero_top + hero_h - 1
            painter.setPen(QPen(accent, 2))
            painter.drawLine(int(ux1), int(uy), int(w - self.PAD_X), int(uy))

        # 3) CHART BODY
        if self._locked:
            self._paint_locked_chart(painter, accent, chart_left, chart_top,
                                     chart_w, chart_h)
        elif self._data is None or self._data.is_empty:
            self._paint_empty_chart(painter, chart_left, chart_top, chart_w, chart_h)
        else:
            self._paint_bands(painter, accent, chart_left, chart_top, chart_w,
                              chart_h)

        # 5) BASELINE + AXIS labels
        painter.setPen(QPen(Colors.TEXT_MUTED, 1))
        painter.drawLine(int(chart_left), int(chart_bottom),
                         int(chart_left + chart_w), int(chart_bottom))
        if self._data is not None and self._data.buckets:
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(f_tiny)
            ax_y = chart_bottom + 2
            painter.drawText(QRectF(chart_left, ax_y, 120, 12),
                             Qt.AlignmentFlag.AlignLeft, self._data.buckets[0])
            painter.drawText(QRectF(chart_left + chart_w - 120, ax_y, 120, 12),
                             Qt.AlignmentFlag.AlignRight, self._data.buckets[-1])

        # 6) LEGEND-SPINE
        legend_top = chart_bottom + 10
        if self._locked or self._data is None:
            self._paint_locked_legend(painter, legend_top)
        elif self._data.is_empty:
            self._paint_empty_legend(painter, legend_top)
        else:
            self._paint_legend(painter, accent, legend_top)

        # 7) RESERVED savings-strip lane — intentionally BLANK (it is #12's).

        # 8) #11 THE AUTOPSY lasso band + dim-outside veil + cursor $-readout —
        #    painted LAST so it sits over the bands (decision A). Only while a
        #    drag is live and not locked/empty.
        if (not self._locked and self._data is not None
                and not self._data.is_empty and self._selection is not None):
            self._paint_lasso(painter, accent, chart_left, chart_top, chart_w,
                              chart_h)

        painter.end()

    def _paint_lasso(self, painter, accent, cl, ct, cw, ch):
        """The selection band (translucent accent) + a BG_DARK veil dimming
        OUTSIDE it + two 1px accent edges + a floating $-readout following the
        cursor (the running Σ of touched buckets). Event-driven (one update() per
        mouseMove) — no QTimer, no per-frame alloc beyond these transient rects."""
        x_left, x_right = self._selection
        x_left = max(cl, min(cl + cw, x_left))
        x_right = max(cl, min(cl + cw, x_right))
        if x_right < x_left:
            x_left, x_right = x_right, x_left
        chart_bottom = ct + ch
        band = QRectF(x_left, ct, x_right - x_left, ch)

        # dim OUTSIDE the band (TimelineChart veil idiom, but vertical).
        veil = QColor(Colors.BG_DARK)
        veil.setAlpha(120)
        if x_left > cl:
            painter.fillRect(QRectF(cl, ct, x_left - cl, ch), veil)
        if x_right < cl + cw:
            painter.fillRect(QRectF(x_right, ct, (cl + cw) - x_right, ch), veil)

        # the translucent selection band.
        sel = QColor(accent)
        sel.setAlpha(40)
        painter.fillRect(band, sel)
        # two 1px vertical accent edges (RoundCap).
        edge = QColor(accent)
        painter.setPen(QPen(edge, 1.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x_left, ct), QPointF(x_left, chart_bottom))
        painter.drawLine(QPointF(x_right, ct), QPointF(x_right, chart_bottom))

        # the floating cursor $-readout (running Σ of touched buckets).
        total = self._selection_sum()
        txt = f"${total:,.2f}"
        f_tiny = Fonts.tiny()
        fm = QFontMetrics(f_tiny)
        pad = 4
        tw = fm.horizontalAdvance(txt) + 2 * pad
        th = fm.height() + 2
        cx = self._drag_x if self._drag_x is not None else (x_left + x_right) / 2.0
        rx = cx + 8
        if rx + tw > cl + cw:
            rx = cx - 8 - tw           # flip to the left if it would overflow
        rx = max(cl, min(cl + cw - tw, rx))
        ry = ct + 4
        chip = QRectF(rx, ry, tw, th)
        chip_bg = QColor(Colors.BG_DARK)
        chip_bg.setAlpha(220)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(_rounded(chip, 3), QBrush(chip_bg))
        painter.setPen(QPen(QColor(accent), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_rounded(chip, 3))
        painter.setPen(Colors.TEXT_PRIMARY)
        painter.setFont(f_tiny)
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, txt)

    def _data_range_label(self):
        # The widget receives only the spectrum; default to the standing label.
        return "Last 7 Days"

    def _paint_bands(self, painter, accent, cl, ct, cw, ch):
        chart_bottom = ct + ch
        data = self._data
        # spike glow column behind the bands
        if self._spike_rect is not None:
            glow = QColor(accent)
            glow.setAlpha(28)
            painter.fillRect(self._spike_rect, glow)
        # bands bottom→top; reveal scales each band height from the baseline
        rv = self._reveal
        for rank, (mid, poly) in enumerate(self._band_polys):
            color = spend_palette.model_color(mid, rank)
            draw_poly = poly
            if rv < 1.0:
                # scale toward baseline so bands "grow" on reveal
                draw_poly = QPolygonF([
                    QPointF(p.x(), chart_bottom - (chart_bottom - p.y()) * rv)
                    for p in poly
                ])
            grad = QLinearGradient(0, ct, 0, chart_bottom)
            c_top = QColor(color); c_top.setAlpha(110)
            c_bot = QColor(color); c_bot.setAlpha(18)
            grad.setColorAt(0, c_top)
            grad.setColorAt(1, c_bot)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawPolygon(draw_poly)
            # crisp full-alpha top stroke (so thin ribbons stay visible)
            stroke = QColor(color)
            if self._hover_model is not None and self._hover_model != mid:
                stroke.setAlpha(120)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(stroke, self.BAND_TOP_STROKE,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            n = len(data.buckets)
            top_n = n if n >= 1 else 0
            if n == 1:
                # the flat top edge of the single column
                painter.drawLine(draw_poly[0], draw_poly[1])
            else:
                top_path = QPainterPath()
                top_path.moveTo(draw_poly[0])
                for i in range(1, top_n):
                    top_path.lineTo(draw_poly[i])
                painter.drawPath(top_path)
        # spike caret + tiny $ label at the top of the spike column
        if self._spike_rect is not None and rv >= 0.999:
            si = data.spike_index
            sx = self._spike_rect.center().x()
            caret_y = ct - 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(accent))
            caret = QPolygonF([
                QPointF(sx - 4, caret_y - 5),
                QPointF(sx + 4, caret_y - 5),
                QPointF(sx, caret_y),
            ])
            painter.drawPolygon(caret)
            painter.setPen(Colors.TEXT_SECONDARY)
            painter.setFont(Fonts.tiny())
            painter.drawText(QRectF(sx - 30, caret_y - 18, 60, 12),
                             Qt.AlignmentFlag.AlignCenter,
                             f"${data.spike_total:,.1f}")

    def _paint_empty_chart(self, painter, cl, ct, cw, ch):
        # flat baseline already drawn by caller; centered tidy message
        painter.setPen(Colors.TEXT_MUTED)
        painter.setFont(Fonts.body())
        painter.drawText(QRectF(cl, ct, cw, ch),
                         Qt.AlignmentFlag.AlignCenter, "No spend in this range")

    def _paint_locked_chart(self, painter, accent, cl, ct, cw, ch):
        chart_bottom = ct + ch
        # faint ghosted stacked-spectrum silhouette (a teaser, NOT data):
        # three greyed bands at ~12% alpha TEXT_MUTED.
        ghost = QColor(Colors.TEXT_MUTED)
        ghost.setAlpha(31)  # ~12%
        fractions = [0.5, 0.78, 0.93]  # cumulative tops of 3 fake bands
        prev = 0.0
        n = 5
        xs = [cl + cw * i / (n - 1) for i in range(n)]
        # a gently wavy silhouette so it reads as "a chart could be here"
        import math as _m
        for bi, frac in enumerate(fractions):
            top_pts, bot_pts = [], []
            for i in range(n):
                wob = 0.06 * _m.sin(i * 1.3 + bi)
                ct_cum = min(1.0, frac + wob)
                cb_cum = prev
                top_pts.append(QPointF(xs[i], chart_bottom - (ch - 4) * ct_cum))
                bot_pts.append(QPointF(xs[i], chart_bottom - (ch - 4) * cb_cum))
            poly = QPolygonF(top_pts + list(reversed(bot_pts)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ghost))
            painter.drawPolygon(poly)
            prev = frac
        # centered padlock + unlock line
        painter.setFont(Fonts.body())
        msg = SPEND_UNLOCK_BASE + " ground-truth spend"
        fm = QFontMetrics(Fonts.body())
        msg_w = fm.horizontalAdvance(msg)
        cx = cl + cw / 2.0
        cy = ct + ch / 2.0
        _paint_padlock(painter, cx - msg_w / 2.0 - 12, cy, 14, Colors.TEXT_MUTED)
        painter.setPen(Colors.TEXT_MUTED)
        painter.drawText(QRectF(cl, cy - fm.height() / 2.0, cw, fm.height()),
                         Qt.AlignmentFlag.AlignCenter, msg)

    def _paint_legend(self, painter, accent, legend_top):
        data = self._data
        row_h = self._legend_row_h_cached
        w = self.width()
        f_body = Fonts.body()
        f_mono = Fonts.mono_small()
        f_tiny = Fonts.tiny()
        fm_body = QFontMetrics(f_body)
        fm_mono = QFontMetrics(f_mono)
        rows = min(len(data.models), self.MAX_LEGEND_ROWS)
        for idx in range(rows):
            m = data.models[idx]
            y = legend_top + idx * row_h
            mid_y = y + row_h / 2.0
            # swatch
            sw = self.SWATCH
            sw_rect = QRectF(self.PAD_X, mid_y - sw / 2.0, sw, sw)
            color = spend_palette.model_color(m.model_id, idx)
            sw_path = QPainterPath()
            sw_path.addRoundedRect(sw_rect, 3, 3)
            painter.fillPath(sw_path, QBrush(color))
            # right side: $ amount + (share%)
            amt = f"${m.total_usage:,.2f}"
            share = f"({m.share * 100:.0f}%)"
            amt_w = fm_mono.horizontalAdvance(amt)
            share_w = QFontMetrics(f_tiny).horizontalAdvance(share)
            right = w - self.PAD_X
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(f_tiny)
            painter.drawText(QRectF(right - share_w, y, share_w, row_h),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             share)
            amt_x = right - share_w - 6 - amt_w
            painter.setPen(Colors.TEXT_PRIMARY)
            painter.setFont(f_mono)
            painter.drawText(QRectF(amt_x, y, amt_w, row_h),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             amt)
            # name (elided to the gap between swatch and the $ block)
            name_x = self.PAD_X + sw + 6
            name_avail = max(10, int(amt_x - 6 - name_x))
            name = fm_body.elidedText(m.short_name, Qt.TextElideMode.ElideRight,
                                      name_avail)
            painter.setPen(Colors.TEXT_SECONDARY if idx > 0 else Colors.TEXT_PRIMARY)
            painter.setFont(f_body)
            painter.drawText(QRectF(name_x, y, name_avail, row_h),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             name)
        # overflow "+N more"
        if len(data.models) > self.MAX_LEGEND_ROWS:
            extra = len(data.models) - self.MAX_LEGEND_ROWS
            y = legend_top + self.MAX_LEGEND_ROWS * row_h
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(f_tiny)
            painter.drawText(QRectF(self.PAD_X, y, w - 2 * self.PAD_X, row_h),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             f"+{extra} more")

    def _paint_empty_legend(self, painter, legend_top):
        # a single tidy row, real zero — NOT the locked placeholder
        row_h = self._legend_row_h_cached
        painter.setPen(Colors.TEXT_MUTED)
        painter.setFont(Fonts.body())
        painter.drawText(QRectF(self.PAD_X, legend_top, self.width() - 2 * self.PAD_X,
                                row_h),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         "—")

    def _paint_locked_legend(self, painter, legend_top):
        row_h = self._legend_row_h_cached
        w = self.width()
        for idx in range(3):
            y = legend_top + idx * row_h
            mid_y = y + row_h / 2.0
            sw = self.SWATCH
            sw_rect = QRectF(self.PAD_X, mid_y - sw / 2.0, sw, sw)
            ghost = QColor(Colors.TEXT_MUTED)
            ghost.setAlpha(80)
            painter.setPen(QPen(ghost, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(sw_rect, 3, 3)
            painter.setPen(Colors.TEXT_MUTED)
            painter.setFont(Fonts.mono_small())
            painter.drawText(QRectF(w - self.PAD_X - 60, y, 60, row_h),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             "— — —")

    # ------------------------------------------------------------------
    #  Interaction (click entry points for #10 / #11) — with the #11 lasso
    # ------------------------------------------------------------------
    @staticmethod
    def _pos(event):
        return event.position() if hasattr(event, "position") \
            else QPointF(event.pos())

    def _gpos(self, event):
        return event.globalPosition() if hasattr(event, "globalPosition") \
            else QPointF(self.mapToGlobal(event.pos()))

    def mouseMoveEvent(self, event):
        if self._locked or self._data is None:
            return
        pos = self._pos(event)
        # A live lasso drag: track the cursor x, paint the band (one update()
        # per move — event-driven, no QTimer). Σ is from cached data (no network).
        if self._drag_x0 is not None:
            cl, _, cw, _ = self._chart_geom()
            self._drag_x = max(cl, min(cl + cw, pos.x()))
            self._selection = (self._drag_x0, self._drag_x)
            self.update()
            super().mouseMoveEvent(event)
            return
        hov = None
        for mid, r in self._legend_rects:
            if r.contains(pos):
                hov = mid
                break
        if hov != self._hover_model:
            self._hover_model = hov
            self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        # LOCKED / empty -> the lasso is DISABLED and there are no click targets.
        if self._locked or self._data is None or self._data.is_empty:
            super().mousePressEvent(event)
            return
        pos = self._pos(event)
        gpos = self._gpos(event)
        # 1) LEGEND ROW first -> #10 receipt; do NOT start a lasso (decision A:
        #    a legend click must never be swallowed by the autopsy). Legend rows
        #    sit BELOW the chart body so they never collide with the lasso zone.
        for mid, r in self._legend_rects:
            if r.contains(pos):
                self.band_clicked.emit(mid, gpos)
                return
        # 2) CHART BODY -> begin a POTENTIAL lasso. The tap-vs-drag split is
        #    resolved on release: the band only paints once the cursor moves, so
        #    a plain click looks identical to today until it crosses DRAG_MIN_PX.
        if self._chart_body_rect().contains(pos):
            self._drag_x0 = pos.x()
            self._drag_x = pos.x()
            self._selection = None          # nothing to dim until a real drag
            self._press_gpos = gpos         # for the tap-fallthrough band_clicked
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        # Not in a lasso (or locked): nothing #11-specific to do.
        if self._drag_x0 is None:
            super().mouseReleaseEvent(event)
            return
        pos = self._pos(event)
        x0 = self._drag_x0
        x1 = pos.x()
        width = abs(x1 - x0)
        gpos = getattr(self, "_press_gpos", None) or self._gpos(event)
        # clear lasso state BEFORE emitting (a re-entrant handler sees a clean slate)
        self._drag_x0 = None
        self._drag_x = None
        had_band = self._selection is not None
        self._selection = None

        if width > self.DRAG_MIN_PX:
            # A real LASSO -> emit the selected bucket window (decision A).
            self._drag_x0 = x0          # transient: feed the inverse helper
            self._drag_x = x1
            win = self._selection_window_iso()
            self._drag_x0 = None
            self._drag_x = None
            if win is not None:
                self.spike_selected.emit(win[0], win[1])
            if had_band:
                self.update()            # erase the band
            return

        # A sub-threshold TAP -> fall through to the OLD single-bucket hit-test
        # (spike column -> spike_clicked, else a band polygon -> band_clicked),
        # so #9's spike tap and #10's band click are exactly as before.
        if had_band:
            self.update()
        if self._spike_rect is not None and self._spike_rect.contains(pos):
            si = self._data.spike_index
            if 0 <= si < len(self._data.buckets):
                t0 = self._data.buckets[si]
                t1 = (self._data.buckets[si + 1]
                      if si + 1 < len(self._data.buckets) else t0)
                self.spike_clicked.emit(t0, t1)
                return
        for mid, poly in self._band_polys:
            if poly.containsPoint(pos, Qt.FillRule.OddEvenFill):
                self.band_clicked.emit(mid, gpos)
                return
        super().mouseReleaseEvent(event)


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


# ---------------------------------------------------------------------------
#  THE TILL ROLL (#10) — per-model receipt stubs + the full thermal receipt
# ---------------------------------------------------------------------------
# The ONE intentional light surface in the dark app — kept ONLY inside the
# receipt object so it reads as "paper", not a broken theme (decision E). The
# stub wash is this parchment at ~10% over BG_CARD; the full receipt pixmap is
# the one fully-light surface.
RECEIPT_PARCHMENT = QColor(0xEC, 0xE7, 0xDE)   # warm paper
RECEIPT_INK = QColor(0x1A, 0x1A, 0x22)         # near-black thermal ink
RECEIPT_PAPER_EDGE = QColor(0xD8, 0xD1, 0xC4)  # a faint paper shadow on the tears


def _fmt_tok(n: int) -> str:
    """Compact token count for a receipt line ('6,915' / '1.2M')."""
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    return f"{n:,}"


class ReceiptStubList(QWidget):
    """THE TILL ROLL stub list (#10): a thin stacked list of per-model receipt
    'stubs' (one ~26px row per active model) directly under #9's Spectrum, each a
    faint parchment strip with a left perforated edge, the model short-name, a
    right-aligned avg $/call in accent, a 7-tick cost-per-call micro-sparkline,
    and — when the latest day's $/call silently spiked vs the trailing median — a
    small rotated RED 'x{mult} PRICE UP' stamp (GREEN on a downshift).

    Measure-then-paint (PinnedModelCard idiom): one _measure() pass derives both
    the cached row geometry and setFixedHeight so nothing clips. The whole row is
    the hit target -> receipt_clicked(model_id, global_anchor) opens the full
    thermal receipt in the shared ProviderPopup. Shares #9's name-column metric
    so the two read as one block.

    Motion: a ONE-TIME 'print' wipe reveal on first data arrival (a held
    QPropertyAnimation on a distinct `print_reveal` float — NOT a QWidget builtin
    — that the paint clips each row to, top-to-bottom like a receipt printing),
    skipped if animations are off / the widget isn't visible, and re-gated by a
    signature compare so a 15-min identical poll doesn't re-animate.
    """

    receipt_clicked = Signal(str, QPointF)

    # -- geometry constants (the measure pass derives the rest) --
    PAD_X = 14
    GUTTER = 10          # left perforation gutter width
    ROW_PAD = 4          # vertical pad inside a row (row_h = text + 2*ROW_PAD)
    ROW_GAP = 4          # gap between stacked stubs
    SPARK_W = 48         # the micro-sparkline slot width
    PERF_PITCH = 5.0     # perforation dot pitch down the gutter
    PERF_D = 2.0         # perforation dot diameter

    def __init__(self, parent=None):
        super().__init__(parent)
        self._receipts = ()       # tuple[Receipt]
        self._locked = False
        self._signature = None
        self._print_reveal = 1.0  # 0..1 top-to-bottom print wipe (animated once)
        # Cached geometry (rebuilt in set_data/resize) — never alloc in paint.
        self._row_h = 0
        self._row_rects = []      # list[(model_id, QRectF)] full-row hit targets
        self._spark_pts = []      # list[list[QPointF]] aligned to _row_rects
        self._stamp_rects = []    # list[QRectF|None] aligned to _row_rects
        self._name_col_w = 0      # shared with #9's legend name column metric

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # ONE held animation (ArcGauge idiom) — never per-frame alloc.
        self._anim = QPropertyAnimation(self, b"print_reveal")
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._measure()
        theme_controller.changed.connect(self._on_theme_changed)

    # -- the print-reveal Property (distinct name; NOT a QWidget builtin) --
    def get_print_reveal(self):
        return self._print_reveal

    def set_print_reveal(self, v):
        self._print_reveal = float(v)
        self.update()

    print_reveal = Property(float, get_print_reveal, set_print_reveal)

    def _on_theme_changed(self):
        self._build_geometry()
        self.update()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def set_data(self, receipts):
        """receipts: a tuple[Receipt] (board.receipts) or None (keep last-good)."""
        if receipts is None:
            return
        self._locked = False
        self._receipts = tuple(receipts)
        sig = self._compute_signature(self._receipts)
        first_or_changed = (sig != self._signature)
        self._signature = sig
        self._measure()
        self._build_geometry()
        if first_or_changed and self._receipts:
            self._start_print()
        else:
            self._print_reveal = 1.0
        self.update()

    def set_locked(self):
        """No management key: a SINGLE dim parchment ghost stub + dotted outline
        + the canonical unlock copy. No rows, no prices, ZERO fake numbers."""
        self._locked = True
        self._receipts = ()
        self._signature = None
        self._print_reveal = 1.0
        self._measure()
        self._row_rects = []
        self._spark_pts = []
        self._stamp_rects = []
        self.update()

    def receipt_for(self, model_id):
        for r in self._receipts:
            if r.model_id == model_id:
                return r
        return None

    # ------------------------------------------------------------------
    #  Print-reveal animation
    # ------------------------------------------------------------------
    def _start_print(self):
        try:
            import anim
            on = anim.ANIMATIONS_ON
        except Exception:
            on = True
        self._anim.stop()
        if not on or not self.isVisible():
            self._print_reveal = 1.0
            return
        self._print_reveal = 0.0
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    @staticmethod
    def _compute_signature(receipts):
        return tuple(
            (r.model_id, round(r.per_call, 6), r.stamp_dir, round(r.stamp_mult, 2))
            for r in receipts
        )

    # ------------------------------------------------------------------
    #  Measure pass (drives BOTH paint and setFixedHeight — no clip)
    # ------------------------------------------------------------------
    def _row_height(self) -> int:
        fm_body = QFontMetrics(Fonts.body())
        fm_mono = QFontMetrics(Fonts.mono_small())
        text_h = max(fm_body.height(), fm_mono.height())
        return int(text_h + 2 * self.ROW_PAD)

    def _n_rows(self) -> int:
        if self._locked:
            return 1                       # the single ghost stub
        return max(1, len(self._receipts)) if self._receipts else 1

    def _measure(self):
        self._row_h = self._row_height()
        n = self._n_rows()
        total = n * (self._row_h + self.ROW_GAP)
        self.setFixedHeight(int(total))

    # ------------------------------------------------------------------
    #  Geometry build (cache row rects + sparkline points + stamp rects)
    # ------------------------------------------------------------------
    def _price_col_w(self) -> int:
        return QFontMetrics(Fonts.mono_small()).horizontalAdvance("$0.0000/call")

    def _build_geometry(self):
        self._row_rects = []
        self._spark_pts = []
        self._stamp_rects = []
        if self._locked or not self._receipts:
            return
        w = max(1, self.width())
        row_h = self._row_h
        price_w = self._price_col_w()
        fm_stamp = QFontMetrics(Fonts.tiny())
        for idx, r in enumerate(self._receipts):
            y = idx * (row_h + self.ROW_GAP)
            row_rect = QRectF(0, y, w, row_h)
            self._row_rects.append((r.model_id, row_rect))

            # right edge: price column (right-padded by PAD_X).
            price_right = w - self.PAD_X
            price_left = price_right - price_w
            # stamp slot sits just LEFT of the price when triggered.
            stamp_rect = None
            stamp_left = price_left
            if r.has_stamp:
                stamp_txt = f"x{_stamp_mult_label(r.stamp_mult)}"
                sw = fm_stamp.horizontalAdvance(stamp_txt) + 14
                sh = row_h - 8
                stamp_rect = QRectF(price_left - 8 - sw, y + 4, sw, sh)
                stamp_left = stamp_rect.left()
            self._stamp_rects.append(stamp_rect)

            # sparkline slot sits left of the stamp/price block.
            spark_right = stamp_left - 8
            spark_left = spark_right - self.SPARK_W
            spark = r.spark
            pts = []
            if len(spark) >= 2:
                mn, mx = min(spark), max(spark)
                rng = (mx - mn) if mx != mn else 1.0
                sp_top = y + 5.0
                sp_bot = y + row_h - 5.0
                sp_h = sp_bot - sp_top
                n = len(spark)
                for i, v in enumerate(spark):
                    px = spark_left + (self.SPARK_W) * i / (n - 1)
                    py = sp_bot - sp_h * (v - mn) / rng
                    pts.append(QPointF(px, py))
            self._spark_pts.append(pts)

        # name column: from the left gutter to the sparkline slot. Mirror #9 by
        # using the same Fonts.body() metric for elision.
        name_left = self.PAD_X + self.GUTTER + 6
        # the narrowest available name width across rows (so all elide alike).
        if self._spark_pts:
            min_spark_left = min(
                (pts[0].x() if pts else (self.width() - self.PAD_X
                                         - self._price_col_w() - self.SPARK_W))
                for pts in self._spark_pts
            )
        else:
            min_spark_left = self.width() - self.PAD_X - self._price_col_w() - self.SPARK_W
        self._name_col_w = max(20, int(min_spark_left - 8 - name_left))

    def resizeEvent(self, event):
        self._build_geometry()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    #  Paint (allocation-light: cached rects/points + cached strokes)
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._locked:
            self._paint_locked(p)
            p.end()
            return
        if not self._receipts:
            p.end()
            return
        accent = theme_controller.accent()
        rv = self._print_reveal
        f_body = Fonts.body()
        f_mono = Fonts.mono_small()
        f_tiny = Fonts.tiny()
        fm_body = QFontMetrics(f_body)
        fm_mono = QFontMetrics(f_mono)
        for idx, (mid, row_rect) in enumerate(self._row_rects):
            r = self._receipts[idx]
            # --- the one-time 'print' wipe: clip each row to a growing top edge.
            if rv < 1.0:
                p.save()
                clip_h = row_rect.height() * rv
                p.setClipRect(QRectF(row_rect.left(), row_rect.top(),
                                     row_rect.width(), clip_h))
            self._paint_row(p, idx, r, row_rect, accent, f_body, f_mono, f_tiny,
                            fm_body, fm_mono)
            if rv < 1.0:
                p.restore()
        p.end()

    def _paint_row(self, p, idx, r, row_rect, accent, f_body, f_mono, f_tiny,
                   fm_body, fm_mono):
        # 1) faint parchment wash (the stub reads as paper, not a broken theme).
        wash = QColor(RECEIPT_PARCHMENT)
        wash.setAlpha(26)   # ~10%
        body = QRectF(row_rect.left() + 1, row_rect.top(), row_rect.width() - 2,
                      row_rect.height())
        path = QPainterPath()
        path.addRoundedRect(body, 4, 4)
        p.fillPath(path, QBrush(wash))

        # 2) the LEFT perforated edge — a column of dots down the gutter.
        dot = QColor(Colors.TEXT_MUTED)
        dot.setAlpha(128)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(dot))
        gx = row_rect.left() + self.PAD_X + self.GUTTER / 2.0
        yy = row_rect.top() + self.PERF_PITCH
        while yy < row_rect.bottom() - 1:
            p.drawEllipse(QPointF(gx, yy), self.PERF_D / 2.0, self.PERF_D / 2.0)
            yy += self.PERF_PITCH

        # 3) model short-name, elided to the shared name column.
        name_left = row_rect.left() + self.PAD_X + self.GUTTER + 6
        name = fm_body.elidedText(r.short_name, Qt.TextElideMode.ElideRight,
                                  self._name_col_w)
        p.setPen(Colors.TEXT_PRIMARY)
        p.setFont(f_body)
        p.drawText(QRectF(name_left, row_rect.top(), self._name_col_w,
                          row_rect.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   name)

        # 4) the 7-tick cost-per-call micro-sparkline (accent + end-dot).
        pts = self._spark_pts[idx]
        if len(pts) >= 2:
            p.setPen(QPen(accent, 1.4, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            sp_path = QPainterPath()
            sp_path.moveTo(pts[0])
            for q in pts[1:]:
                sp_path.lineTo(q)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(sp_path)
            p.setBrush(QBrush(accent))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(pts[-1], 1.8, 1.8)

        # 5) the STAMP (still, not pulsing — a still stamp reads 'official').
        stamp_rect = self._stamp_rects[idx]
        if stamp_rect is not None:
            self._paint_stamp(p, stamp_rect, r, f_tiny)

        # 6) right-aligned avg $/call in accent mono.
        price_txt = f"${r.per_call:.4f}/call"
        price_w = self._price_col_w()
        price_right = row_rect.right() - self.PAD_X
        p.setPen(QPen(accent))
        p.setFont(f_mono)
        p.drawText(QRectF(price_right - price_w, row_rect.top(), price_w,
                          row_rect.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   price_txt)

    def _paint_stamp(self, p, rect, r, f_tiny):
        color = Colors.RED if r.stamp_dir > 0 else Colors.GREEN
        p.save()
        # rotate -7deg about the stamp center for the hand-stamped look.
        c = rect.center()
        p.translate(c)
        p.rotate(-7)
        p.translate(-c)
        fill = QColor(color)
        fill.setAlpha(46)   # ~18%
        path = QPainterPath()
        path.addRoundedRect(rect, 3, 3)
        p.fillPath(path, QBrush(fill))
        p.setPen(QPen(color, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(QPen(color))
        p.setFont(f_tiny)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                   f"x{_stamp_mult_label(r.stamp_mult)}")
        p.restore()

    def _paint_locked(self, p):
        # a single dim parchment ghost stub with a DOTTED outline + unlock copy.
        w = self.width()
        row_rect = QRectF(1, 0, w - 2, self._row_h)
        wash = QColor(RECEIPT_PARCHMENT)
        wash.setAlpha(16)
        path = QPainterPath()
        path.addRoundedRect(row_rect, 4, 4)
        p.fillPath(path, QBrush(wash))
        p.setPen(QPen(Colors.TEXT_MUTED, 1, Qt.PenStyle.DotLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(row_rect, 4, 4)
        msg = SPEND_UNLOCK_BASE + " · print per-call receipts"
        fm = QFontMetrics(Fonts.body())
        msg = fm.elidedText(msg, Qt.TextElideMode.ElideRight, int(w - 2 * self.PAD_X - 18))
        msg_w = fm.horizontalAdvance(msg)
        cx = w / 2.0
        cy = self._row_h / 2.0
        _paint_padlock(p, cx - msg_w / 2.0 - 11, cy, 13, Colors.TEXT_MUTED)
        p.setPen(Colors.TEXT_MUTED)
        p.setFont(Fonts.body())
        p.drawText(row_rect, Qt.AlignmentFlag.AlignCenter, msg)

    # ------------------------------------------------------------------
    #  Interaction — whole row opens the full thermal receipt
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if self._locked or not self._receipts:
            super().mousePressEvent(event)
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        gpos = event.globalPosition() if hasattr(event, "globalPosition") \
            else QPointF(self.mapToGlobal(event.pos()))
        for mid, rect in self._row_rects:
            if rect.contains(pos):
                self.receipt_clicked.emit(mid, gpos)
                return
        super().mousePressEvent(event)


def _stamp_mult_label(mult: float) -> str:
    """Compact stamp multiplier ('3.1', '12', '0.4')."""
    if mult >= 10:
        return f"{mult:.0f}"
    return f"{mult:.1f}"


class ReceiptStripWidget(QWidget):
    """The full itemized THERMAL RECEIPT (#10 click-through), rendered to a
    QPixmap and embedded as a data-URI <img> in the shared ProviderPopup
    (UptimeStripWidget idiom). The ONE fully-light surface in the app — warm
    parchment with near-black mono ink.

    Honest by construction (decision A): the line items show real AVERAGE token
    COUNTS per call (INPUT / OUTPUT / REASONING — token counts, NO per-line $);
    the only itemized $ are the GREEN cache CREDIT (abs(usage_cache)/calls) and
    the bold SUBTOTAL / CALL (total_usage/calls). A RED 'PRICE UP Nx vs 7d
    MEDIAN' banner prints only when the receipt's stamp fired; a deterministic
    barcode is seeded from the model-id hash. Header label '7-DAY AVG / CALL' so
    nothing is implied as one real call.

    devicePixelRatio-aware: the pixmap is rendered at the device ratio so the
    mono text stays crisp on a HiDPI display; the <img> is sized in logical px.
    """

    STRIP_W = 300

    def __init__(self, receipt, parent=None):
        super().__init__(parent)
        self._r = receipt
        self._h = self._measure_height()
        self.setFixedSize(self.STRIP_W, self._h)

    # -- measure (font-metric-driven; one pass shared by paint + the pixmap) --
    def _line_h(self) -> int:
        return QFontMetrics(Fonts.mono_small()).height() + 3

    def _line_items(self):
        """The ordered (label, value, role) line items. role: 'tok' (count, no
        $), 'credit' (green $), 'sub' (bold subtotal $), 'tie' (muted)."""
        r = self._r
        items = [
            (f"INPUT  {_fmt_tok(r.avg_prompt_tok)} tok", None, "tok"),
            (f"OUTPUT {_fmt_tok(r.avg_completion_tok)} tok", None, "tok"),
        ]
        if r.avg_reasoning_tok > 0:
            items.append((f"REASON {_fmt_tok(r.avg_reasoning_tok)} tok", None, "tok"))
        if r.cache_credit_per_call > 0 or r.avg_cached_tok > 0:
            items.append((f"CACHE READ {_fmt_tok(r.avg_cached_tok)} tok",
                          f"-${r.cache_credit_per_call:.4f}", "credit"))
        return items

    def _measure_height(self) -> int:
        line_h = self._line_h()
        tear = 8
        header_lines = 3            # name / OPENROUTER·RECEIPT / 7-DAY AVG/CALL
        n_items = len(self._line_items())
        banner = 22 if (self._r is not None and self._r.has_stamp) else 0
        footnote = line_h if (self._r is not None and self._r.young) else 0
        # tear + header + rule + items + rule + subtotal + tie + banner + barcode
        # + joke + tear
        h = (tear + header_lines * line_h + 6
             + n_items * line_h + 6
             + line_h          # subtotal
             + line_h          # "TIMES N CALLS = $total" tie-back
             + banner
             + footnote
             + 22              # barcode block
             + line_h          # joke line
             + tear)
        return int(h)

    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        pm = QPixmap(int(self.STRIP_W * dpr), int(self._h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_tear(self, p, y, w, down: bool):
        """A jagged sawtooth tear (6px teeth) across the paper width."""
        teeth = 6.0
        pts = []
        x = 0.0
        up = y - 4 if down else y
        dn = y if down else y + 4
        toggle = True
        while x <= w:
            pts.append(QPointF(x, dn if toggle else up))
            toggle = not toggle
            x += teeth
        pts.append(QPointF(w, dn if toggle else up))
        poly = QPolygonF(pts)
        p.setPen(QPen(RECEIPT_PAPER_EDGE, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(poly)

    def _paint_into(self, p):
        r = self._r
        w = self.STRIP_W
        h = self._h
        line_h = self._line_h()
        f_mono = Fonts.mono_small()
        fm = QFontMetrics(f_mono)
        pad = 16

        # 1) parchment paper with a top + bottom jagged tear.
        paper = QPainterPath()
        paper.addRect(QRectF(0, 4, w, h - 8))
        p.fillPath(paper, QBrush(RECEIPT_PARCHMENT))
        self._paint_tear(p, 4, w, down=True)
        self._paint_tear(p, h - 4, w, down=False)

        y = 10.0

        def center(text, color=RECEIPT_INK, font=f_mono):
            nonlocal y
            p.setFont(font)
            p.setPen(QPen(color))
            p.drawText(QRectF(0, y, w, line_h),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       text)
            y += line_h

        # 2) centered mono header.
        center(r.short_name if r else "", RECEIPT_INK, f_mono)
        faint = QColor(RECEIPT_INK); faint.setAlpha(120)
        center("OPENROUTER · RECEIPT", faint, Fonts.tiny())
        center("7-DAY AVG / CALL", faint, Fonts.tiny())

        # 3) a dotted rule.
        def rule():
            nonlocal y
            y += 3
            dotted = QColor(RECEIPT_INK); dotted.setAlpha(110)
            p.setPen(QPen(dotted, 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(pad, y), QPointF(w - pad, y))
            y += 3

        rule()

        # 4) LINE ITEMS — left label + dot-leader + right value, flush-right.
        def line_item(label, value, role):
            nonlocal y
            if role == "credit":
                col = QColor(Colors.GREEN)
            else:
                col = RECEIPT_INK
            p.setFont(f_mono)
            # value first so we can size the dot-leader to land flush.
            val_w = fm.horizontalAdvance(value) if value else 0
            label_w = fm.horizontalAdvance(label)
            right = w - pad
            left = pad
            p.setPen(QPen(col))
            p.drawText(QPointF(left, y + fm.ascent()), label)
            if value:
                p.drawText(QRectF(right - val_w, y, val_w, line_h),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                           value)
                # dot leader between label and value.
                lead_l = left + label_w + 4
                lead_r = right - val_w - 4
                if lead_r > lead_l:
                    leader = QColor(RECEIPT_INK); leader.setAlpha(90)
                    p.setPen(QPen(leader, 1, Qt.PenStyle.DotLine))
                    ly = y + fm.ascent() - 3
                    p.drawLine(QPointF(lead_l, ly), QPointF(lead_r, ly))
            y += line_h

        for label, value, role in self._line_items():
            line_item(label, value, role)

        rule()

        # 5) the bold SUBTOTAL / CALL (a REAL $ we know: total_usage/calls).
        sub_font = QFont(f_mono)
        sub_font.setWeight(QFont.Weight.Bold)
        p.setFont(sub_font)
        p.setPen(QPen(RECEIPT_INK))
        sub_val = f"${r.per_call:.4f}" if r else "$0.0000"
        p.drawText(QPointF(pad, y + fm.ascent()), "SUBTOTAL / CALL")
        p.drawText(QRectF(0, y, w - pad, line_h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, sub_val)
        y += line_h

        # 6) tie-back to #9: TIMES N CALLS = $range_total (muted).
        tie = QColor(RECEIPT_INK); tie.setAlpha(120)
        p.setFont(Fonts.tiny())
        p.setPen(QPen(tie))
        p.drawText(QRectF(pad, y, w - 2 * pad, line_h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"TIMES {r.request_count:,} CALLS  =  ${r.total_usage:,.2f}")
        y += line_h

        # 7) the RED 'PRICE UP Nx vs 7d MEDIAN' banner — only when triggered.
        if r and r.has_stamp:
            up = r.stamp_dir > 0
            col = Colors.RED if up else Colors.GREEN
            band = QRectF(pad, y, w - 2 * pad, 18)
            fill = QColor(col); fill.setAlpha(40)
            p.fillRect(band, fill)
            p.setPen(QPen(col, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(band)
            word = "PRICE UP" if up else "PRICE DOWN"
            p.setPen(QPen(col))
            bf = QFont(Fonts.tiny()); bf.setWeight(QFont.Weight.Bold)
            p.setFont(bf)
            p.drawText(band, Qt.AlignmentFlag.AlignCenter,
                       f"{word} {_stamp_mult_label(r.stamp_mult)}x vs 7d MEDIAN")
            y += 22

        # 8) YOUNG-account footnote (no false trigger; building history).
        if r and r.young:
            fn = QColor(RECEIPT_INK); fn.setAlpha(120)
            p.setFont(Fonts.tiny())
            p.setPen(QPen(fn))
            p.drawText(QRectF(pad, y, w - 2 * pad, line_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                       "building history — needs 7 days for a price tripwire")
            y += line_h

        # 9) a deterministic barcode (variable-width bars from the model-id hash).
        self._paint_barcode(p, pad, y, w - 2 * pad, 16)
        y += 22

        # 10) the joke line (muted).
        joke = QColor(RECEIPT_INK); joke.setAlpha(110)
        p.setFont(Fonts.tiny())
        p.setPen(QPen(joke))
        p.drawText(QRectF(0, y, w, line_h),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   "* NOT A TAX RECEIPT *")

    def _paint_barcode(self, p, x, y, w, h):
        import hashlib
        seed = hashlib.md5((self._r.model_id if self._r else "").encode("utf-8")).digest()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(RECEIPT_INK))
        cx = x
        i = 0
        while cx < x + w - 1:
            byte = seed[i % len(seed)]
            bar_w = 1.0 + (byte & 0x03)        # 1..4 px wide
            gap = 1.0 + ((byte >> 2) & 0x03)   # 1..4 px gap
            if (byte >> 4) & 1:                # ~half the slots are bars
                p.drawRect(QRectF(cx, y, bar_w, h))
            cx += bar_w + gap
            i += 1


def build_receipt_html(receipt) -> str:
    """The full thermal-receipt dossier for the ProviderPopup: a header + the
    painted receipt pixmap embedded as a data-URI <img> (single-QLabel contract).
    Every API-sourced string (the model short-name) is html.escape'd before it
    enters the HTML wrapper (decision C); the pixmap text itself is QPainter-drawn
    so it's injection-safe by construction. Returns '' when there's no receipt."""
    if receipt is None:
        return ("<div style='font-size:9.5pt;color:#a0a0c8;font-weight:bold;'>"
                "— NO RECEIPT ON FILE —</div>")
    name = html.escape(receipt.short_name or "")
    out = [f"<div style='font-size:11pt;font-weight:bold;color:#f0f0ff;'>{name}</div>"]
    out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
               "per-call receipt · 7-day average · ground truth</div>")
    try:
        strip = ReceiptStripWidget(receipt)
        pm = strip.render_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        out.append(
            f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
            f"width='{ReceiptStripWidget.STRIP_W}' height='{strip._h}'></div>")
    except Exception:
        log.debug("receipt strip render failed", exc_info=True)
    out.append("<div style='margin-top:2px;color:#64648c;font-size:8pt;'>"
               "live from openrouter.ai · analytics · refreshed every ~15 min</div>")
    return "".join(out)


def receipt_accent_hex(receipt) -> str:
    """The popup border accent for a receipt: RED when the price tripped UP,
    GREEN on a downshift, else the panel accent. Returns a #rrggbb hex."""
    if receipt is not None and receipt.has_stamp:
        col = Colors.RED if receipt.stamp_dir > 0 else Colors.GREEN
        return col.name()
    return theme_controller.accent().name()


# ===========================================================================
#  #12 THE REBATE STUB — the torn money-back coupon at the foot of the receipt
# ===========================================================================
def _fmt_tok_count(n: int) -> str:
    """Compact token count ('6.5K', '6.1M', '254')."""
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class RebateStub(QWidget):
    """THE REBATE STUB (#12): a single ~44px full-width perforated 'rebate stub'
    directly below #10's receipt stubs — the realized cache CREDIT (the NEGATIVE
    usage_cache, already applied to the balance) drawn as a literal money-back
    coupon, NOT a flat stat line.

    Three blocks in one measure-then-paint pass (PinnedModelCard idiom; one
    _measure() feeds both the cached geometry and setFixedHeight==REBATE_H so
    nothing clips):
      LEFT   — the GREEN rebate amount ('$16.28') in mono_medium with a faint
               ghosted minus + an up-left 'money returned' chevron, label
               'CACHING REBATE · 7D'. GREEN is this widget's EXCLUSIVE role.
      CENTER — a hand-painted hit-rate HALF-ARC (a 180° GREEN sweep ∝ hit_rate,
               a miniature echo of the balance ArcGauge) + '93.6% HIT'.
      RIGHT  — a slim PURPLE vertical capsule METER filled to the reasoning
               count normalized vs the period max + '6.5K rsn tok' + the italic
               footnote 'tokens, not $' (so we never imply a reasoning dollar).

    Motion: a ONE-TIME count-up on the amount AND the arc sweep share ONE held
    QPropertyAnimation on a distinct `display_amount` float (NOT a QWidget
    builtin) — 0→1 OutCubic, re-gated by a signature compare so a 15-min
    identical poll doesn't re-animate; skipped when animations are off / hidden.

    Click → rebate_clicked(global_anchor) opens the per-model rebate breakdown
    in the shared ProviderPopup.
    """

    rebate_clicked = Signal(QPointF)

    # -- geometry constants (the measure pass positions content WITHIN this) --
    REBATE_H = 44        # the locked stub silhouette height (TEST_PLAN a)
    PAD_X = 14
    PERF_PITCH = 7.0     # perforation dot pitch across the top tear line
    PERF_D = 3.0         # perforation dot diameter (r=1.5)
    ARC_BOX = 60         # the centered half-arc box width
    ARC_R = 22.0         # half-arc radius
    METER_W = 8          # the purple reasoning capsule width
    METER_H = 24         # the purple reasoning capsule height

    def __init__(self, parent=None):
        super().__init__(parent)
        self._savings = None       # Savings | None
        self._locked = False
        self._signature = None
        self._display_amount = 1.0  # 0..1 count-up/arc-sweep factor (animated once)
        # Cached geometry (rebuilt in set_data/resize) — never alloc in paint.
        self._left_rect = QRectF()      # the amount block
        self._arc_box = QRectF()        # the centered half-arc box
        self._meter_rect = QRectF()     # the purple capsule
        self._right_block_left = 0.0    # left edge of the right (meter+label) block

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

        # ONE held animation (ArcGauge idiom) — never per-frame alloc.
        self._anim = QPropertyAnimation(self, b"display_amount")
        self._anim.setDuration(600)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._measure()
        self._build_geometry()
        theme_controller.changed.connect(self._on_theme_changed)

    # -- the count-up Property (distinct name; NOT a QWidget builtin) --
    def get_display_amount(self):
        return self._display_amount

    def set_display_amount(self, v):
        self._display_amount = float(v)
        self.update()

    display_amount = Property(float, get_display_amount, set_display_amount)

    def _on_theme_changed(self):
        self._build_geometry()
        self.update()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def set_data(self, savings):
        """savings: a Savings (board.savings) or None (keep last-good)."""
        if savings is None:
            return
        self._locked = False
        self._savings = savings
        sig = self._compute_signature(savings)
        first_or_changed = (sig != self._signature)
        self._signature = sig
        self._measure()
        self._build_geometry()
        # One-time count-up only on first populated render / when the numbers
        # change (a 15-min identical poll is silent). Honor the animations flag.
        if first_or_changed and not savings.is_empty:
            self._start_count_up()
        else:
            self._display_amount = 1.0
        self.update()

    def set_locked(self):
        """No management key: the same 44px stub silhouette greyed (perforation
        + outline TEXT_MUTED, NO green); amount slot = padlock + the canonical
        unlock copy; arc + meter draw as empty outlines only. ZERO fake $."""
        self._locked = True
        self._savings = None
        self._signature = None
        self._display_amount = 1.0
        self._measure()
        self._build_geometry()
        self.update()

    # ------------------------------------------------------------------
    #  Count-up animation (amount + arc sweep share ONE held animation)
    # ------------------------------------------------------------------
    def _start_count_up(self):
        try:
            import anim
            on = anim.ANIMATIONS_ON
        except Exception:
            on = True
        self._anim.stop()
        if not on or not self.isVisible():
            self._display_amount = 1.0
            return
        self._display_amount = 0.0
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    @staticmethod
    def _compute_signature(savings):
        if savings is None:
            return None
        return (round(savings.total_rebate, 4),
                round(savings.hit_rate_pct, 2),
                int(savings.reasoning_total))

    # ------------------------------------------------------------------
    #  Measure pass (drives BOTH paint and setFixedHeight==44 — no clip)
    # ------------------------------------------------------------------
    def _measure(self):
        # The stub silhouette is a fixed 44px (the locked + populated heights
        # match so the section never jumps when a key is added). Content blocks
        # are positioned WITHIN this height by font metrics in _build_geometry.
        self.setFixedHeight(self.REBATE_H)

    def _left_block_w(self) -> int:
        # amount glyph width budget + the chevron + margins.
        fm = QFontMetrics(Fonts.mono_medium())
        return fm.horizontalAdvance("$00.00") + 10 + 16

    def _right_block_w(self) -> int:
        # the purple capsule + the widest reasoning label ('6.5K rsn tok').
        fm = QFontMetrics(Fonts.tiny())
        label_w = fm.horizontalAdvance("000.0K rsn tok")
        return self.METER_W + 8 + label_w + self.PAD_X

    def _build_geometry(self):
        h = self.REBATE_H
        w = max(1, self.width())
        # LEFT amount block (label above + amount glyph), left-padded.
        left_w = self._left_block_w()
        self._left_rect = QRectF(self.PAD_X, 0, left_w, h)
        # RIGHT block (capsule + reasoning label), right-aligned.
        right_w = self._right_block_w()
        self._right_block_left = w - right_w
        # the capsule sits at the right block's left edge, vertically centered.
        meter_top = (h - self.METER_H) / 2.0 + 3  # +3 to clear the perforation
        self._meter_rect = QRectF(self._right_block_left, meter_top,
                                  self.METER_W, self.METER_H)
        # CENTER half-arc box, centered between the left + right blocks.
        mid_lo = self._left_rect.right()
        mid_hi = self._right_block_left
        cx = (mid_lo + mid_hi) / 2.0
        self._arc_box = QRectF(cx - self.ARC_BOX / 2.0, 4,
                               self.ARC_BOX, h - 8)

    def resizeEvent(self, event):
        self._build_geometry()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    #  Paint (allocation-light: cached rects + cached strokes)
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_body_and_perforation(p)
        if self._locked:
            self._paint_locked(p)
        else:
            self._paint_populated(p)
        p.end()

    def _paint_body_and_perforation(self, p):
        """The rounded-rect coupon body + the PERFORATED top tear line (dots in
        TEXT_MUTED, plus two half-circle notches cut at the left/right edges in
        the PANEL'S ACTUAL BACKGROUND color — decision D — so the stub reads as
        torn off the receipt above instead of letting the scroll show through)."""
        w = self.width()
        h = self.height()
        # resting border lifts to the panel accent on hover (PointingHand).
        border = theme_controller.accent() if (self._hover and not self._locked) \
            else Colors.BORDER
        body = QPainterPath()
        body.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        p.fillPath(body, QBrush(Colors.BG_CARD))
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(body)

        # The two edge notches at the tear line — filled with the panel bg so the
        # rounded card looks torn (NOT transparent; the scroll content is dark
        # Colors.BG_DARK behind the transparent container).
        notch_y = 6.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(Colors.BG_DARK))
        p.drawEllipse(QPointF(0, notch_y), 4.0, 4.0)
        p.drawEllipse(QPointF(w, notch_y), 4.0, 4.0)

        # The perforation dots across the top tear line (evenly distributed).
        dot = QColor(Colors.TEXT_MUTED)
        dot.setAlpha(230)
        p.setBrush(QBrush(dot))
        inner = w - 16
        steps = max(1, round(inner / self.PERF_PITCH))
        for i in range(steps + 1):
            x = 8 + inner * i / steps
            p.drawEllipse(QPointF(x, notch_y), self.PERF_D / 2.0,
                          self.PERF_D / 2.0)

    # -- the three populated blocks -----------------------------------------
    def _paint_populated(self, p):
        sv = self._savings
        if sv is None:
            return
        f = self._display_amount
        self._paint_amount(p, sv, f)
        self._paint_hit_arc(p, sv, f)
        self._paint_reasoning_meter(p, sv)

    def _paint_amount(self, p, sv, f):
        r = self._left_rect
        f_label = Fonts.label()
        f_amt = Fonts.mono_medium()
        fm_label = QFontMetrics(f_label)
        fm_amt = QFontMetrics(f_amt)
        # vertical stack: label on top, amount below, centered in the stub.
        block_h = fm_label.height() + 2 + fm_amt.height()
        top = (self.REBATE_H - block_h) / 2.0 + 3   # +3 to clear the perforation
        # label 'CACHING REBATE · 7D'
        p.setPen(Colors.TEXT_MUTED)
        p.setFont(f_label)
        p.drawText(QRectF(r.left(), top, r.width(), fm_label.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "CACHING REBATE · 7D")
        # the amount, counting up over f. A faint ghosted minus precedes it; an
        # up-left chevron (filled QPolygonF) sits to its left = 'money returned'.
        amt = sv.total_rebate * f
        amt_txt = f"${amt:,.2f}"
        amt_y = top + fm_label.height() + 2
        # chevron (5px, up-left) to the left of the amount.
        chev_cx = r.left() + 4.0
        chev_cy = amt_y + fm_amt.height() / 2.0
        chev = QPolygonF([
            QPointF(chev_cx + 4, chev_cy - 4),
            QPointF(chev_cx - 4, chev_cy),
            QPointF(chev_cx + 4, chev_cy + 4),
        ])
        green = Colors.GREEN
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(green))
        p.drawPolygon(chev)
        # faint ghosted minus (the negative-usage credit becomes a + for the user)
        amt_left = chev_cx + 9
        ghost = QColor(Colors.TEXT_MUTED); ghost.setAlpha(120)
        p.setFont(f_amt)
        p.setPen(QPen(ghost))
        minus_w = fm_amt.horizontalAdvance("-")
        p.drawText(QRectF(amt_left, amt_y, minus_w, fm_amt.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "-")
        # the GREEN amount
        p.setPen(QPen(green))
        p.drawText(QRectF(amt_left + minus_w, amt_y,
                          r.right() - (amt_left + minus_w), fm_amt.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   amt_txt)

    def _paint_hit_arc(self, p, sv, f):
        """A 180° half-arc (a miniature ArcGauge echo): a TEXT_MUTED track + a
        GREEN sweep proportional to hit_rate (0..100), with '93.6% HIT' under
        it. The swept span is stored on self for the test (TEST_PLAN d)."""
        box = self._arc_box
        cx = box.center().x()
        cy = box.top() + box.height() * 0.46
        r = self.ARC_R
        arc_rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        # 180° track from 180°→0° (a top half-arc).
        start_angle = 180 * 16
        track_pen = QPen(QColor(Colors.TEXT_MUTED), 3, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap)
        track = QColor(Colors.TEXT_MUTED); track.setAlpha(90)
        track_pen.setColor(track)
        p.setPen(track_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(arc_rect, start_angle, -180 * 16)
        # GREEN sweep ∝ hit_rate (×f for the one-time sweep). Stored for the test.
        frac = max(0.0, min(1.0, sv.hit_rate_pct / 100.0)) * f
        self._arc_swept_deg = 180.0 * frac
        span = int(-180 * 16 * frac)
        p.setPen(QPen(Colors.GREEN, 3, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, start_angle, span)
        # '93.6%' + 'HIT' centered under the arc.
        f_tiny = Fonts.tiny()
        fm = QFontMetrics(f_tiny)
        p.setFont(f_tiny)
        p.setPen(Colors.TEXT_PRIMARY)
        pct_txt = f"{sv.hit_rate_pct:.1f}%"
        p.drawText(QRectF(cx - box.width() / 2.0, cy + 1, box.width(),
                          fm.height()),
                   Qt.AlignmentFlag.AlignHCenter, pct_txt)
        p.setPen(Colors.TEXT_MUTED)
        p.drawText(QRectF(cx - box.width() / 2.0, cy + 1 + fm.height(),
                          box.width(), fm.height()),
                   Qt.AlignmentFlag.AlignHCenter, "HIT")

    def _paint_reasoning_meter(self, p, sv):
        """A slim PURPLE vertical capsule filled to the reasoning count
        normalized vs the period max (guarded /0 → a tidy zero-height capsule),
        '6.5K rsn tok' + the italic footnote 'tokens, not $'. PURPLE only, so we
        never imply a reasoning dollar figure (decision D)."""
        m = self._meter_rect
        # capsule track
        track = QColor(Colors.TEXT_MUTED); track.setAlpha(60)
        cap = QPainterPath()
        cap.addRoundedRect(m, self.METER_W / 2.0, self.METER_W / 2.0)
        p.fillPath(cap, QBrush(track))
        # fill: normalized vs the period's max single-day reasoning (guarded /0).
        ref = sv.reasoning_ref
        frac = (sv.reasoning_total / ref) if ref > 0 else 0.0
        frac = max(0.0, min(1.0, frac))
        if frac > 0.0:
            fill_h = m.height() * frac
            fill_rect = QRectF(m.left(), m.bottom() - fill_h, m.width(), fill_h)
            fill = QPainterPath()
            fill.addRoundedRect(fill_rect, self.METER_W / 2.0, self.METER_W / 2.0)
            p.fillPath(fill, QBrush(Colors.PURPLE))
        # labels to the right of the capsule.
        f_tiny = Fonts.tiny()
        fm = QFontMetrics(f_tiny)
        lx = m.right() + 8
        lw = self.width() - self.PAD_X - lx
        count_txt = f"{_fmt_tok_count(sv.reasoning_total)} rsn tok"
        # the count label (the real info), top-aligned to the capsule.
        p.setFont(f_tiny)
        p.setPen(Colors.PURPLE)
        p.drawText(QRectF(lx, m.top() - 1, lw, fm.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   count_txt)
        # the italic 'tokens, not $' footnote below it.
        f_it = Fonts.tiny(); f_it.setItalic(True)
        p.setFont(f_it)
        note = QColor(Colors.TEXT_MUTED)
        p.setPen(QPen(note))
        p.drawText(QRectF(lx, m.top() - 1 + fm.height(), lw, fm.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "tokens, not $")

    def _paint_locked(self, p):
        """Greyed silhouette: padlock + unlock copy where the amount is; the arc
        + meter as empty outlines only. NO green, ZERO fake numbers."""
        # amount slot -> padlock + the canonical unlock copy (decision E).
        r = self._left_rect
        msg = SPEND_UNLOCK_BASE + " cache savings"
        f_body = Fonts.body()
        fm = QFontMetrics(f_body)
        # the copy spans toward the center (it's the dominant locked element).
        copy_left = r.left() + 18
        copy_w = self.width() - self.PAD_X - copy_left
        msg = fm.elidedText(msg, Qt.TextElideMode.ElideRight, int(copy_w))
        cy = self.REBATE_H / 2.0 + 3
        _paint_padlock(p, r.left() + 7, cy, 13, Colors.TEXT_MUTED)
        p.setPen(Colors.TEXT_MUTED)
        p.setFont(f_body)
        p.drawText(QRectF(copy_left, 0, copy_w, self.REBATE_H),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   msg)
        # the arc as an empty outline (track only, no green).
        box = self._arc_box
        cx = box.center().x()
        acy = box.top() + box.height() * 0.46
        rr = self.ARC_R
        track = QColor(Colors.TEXT_MUTED); track.setAlpha(70)
        p.setPen(QPen(track, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(cx - rr, acy - rr, 2 * rr, 2 * rr), 180 * 16, -180 * 16)
        # the meter as an empty capsule outline (no purple fill).
        m = self._meter_rect
        cap = QColor(Colors.TEXT_MUTED); cap.setAlpha(70)
        p.setPen(QPen(cap, 1))
        p.drawRoundedRect(m, self.METER_W / 2.0, self.METER_W / 2.0)

    # ------------------------------------------------------------------
    #  Interaction — the whole strip opens the per-model rebate breakdown
    # ------------------------------------------------------------------
    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._locked or self._savings is None:
            super().mousePressEvent(event)
            return
        gpos = event.globalPosition() if hasattr(event, "globalPosition") \
            else QPointF(self.mapToGlobal(event.pos()))
        self.rebate_clicked.emit(gpos)


class RebateBreakdownStrip(QWidget):
    """The per-model rebate breakdown (#12 click-through), rendered to a QPixmap
    and embedded as a data-URI <img> in the shared ProviderPopup (UptimeStrip
    idiom). A 7-day sparkline of daily abs(usage_cache) on top, then one GREEN
    bar per model that saved (abs(usage_cache)) with its cached-token count +
    per-model hit-rate, sorted by savings desc; footer 'Realized credit, already
    applied to your balance.'

    Honest by construction: every dollar is a realized cache CREDIT; the bar
    length encodes abs(usage_cache) (NEVER a fabricated figure). The model names
    are QPainter-drawn here (injection-safe); the HTML wrapper html.escapes them.
    devicePixelRatio-aware so the text stays crisp on HiDPI.
    """

    STRIP_W = 300

    def __init__(self, savings, parent=None):
        super().__init__(parent)
        self._sv = savings
        self._rows = list(getattr(savings, "models", ()) or [])
        self._h = self._measure_height()
        self.setFixedSize(self.STRIP_W, self._h)

    def _row_h(self) -> int:
        return QFontMetrics(Fonts.tiny()).height() + 8

    def _measure_height(self) -> int:
        spark_h = 22
        n = max(1, len(self._rows))
        footer_h = QFontMetrics(Fonts.tiny()).height() + 6
        return int(8 + spark_h + n * self._row_h() + footer_h + 8)

    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        pm = QPixmap(int(self.STRIP_W * dpr), int(self._h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        w = self.STRIP_W
        f_tiny = Fonts.tiny()
        f_mono = Fonts.mono_small()
        fm_tiny = QFontMetrics(f_tiny)
        pad = 12
        y = 8.0

        # 1) the 7-day daily abs(usage_cache) sparkline (GREEN gradient line).
        spark = list(getattr(self._sv, "spark", ()) or [])
        spark_h = 18.0
        spark_rect = QRectF(pad, y, w - 2 * pad, spark_h)
        if len(spark) >= 2 and max(spark) > 0:
            mn, mx = min(spark), max(spark)
            rng = (mx - mn) if mx != mn else 1.0
            pts = []
            n = len(spark)
            for i, v in enumerate(spark):
                px = spark_rect.left() + spark_rect.width() * i / (n - 1)
                py = spark_rect.bottom() - spark_rect.height() * (v - mn) / rng
                pts.append(QPointF(px, py))
            path = QPainterPath()
            path.moveTo(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            p.setPen(QPen(Colors.GREEN, 1.6, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setBrush(QBrush(Colors.GREEN))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(pts[-1], 2.0, 2.0)
        y += spark_h + 4

        # 2) one GREEN bar per model that saved (abs(usage_cache)).
        row_h = self._row_h()
        max_rebate = max((r.rebate for r in self._rows), default=0.0)
        name_w = 96.0
        bar_left = pad + name_w + 6
        bar_max_w = (w - pad) - bar_left - 64   # leave room for the $ at the end
        for r in self._rows:
            ry = y
            # model short-name (elided), muted.
            p.setFont(f_tiny)
            p.setPen(Colors.TEXT_SECONDARY)
            name = fm_tiny.elidedText(r.short_name, Qt.TextElideMode.ElideRight,
                                      int(name_w))
            p.drawText(QRectF(pad, ry, name_w, row_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       name)
            # GREEN bar ∝ abs(usage_cache).
            frac = (r.rebate / max_rebate) if max_rebate > 0 else 0.0
            bar_w = max(2.0, bar_max_w * frac)
            bar_cy = ry + row_h / 2.0
            bar_rect = QRectF(bar_left, bar_cy - 4, bar_w, 8)
            track = QColor(Colors.GREEN); track.setAlpha(40)
            p.fillPath(_rounded(bar_rect, 4), QBrush(track))
            p.fillPath(_rounded(bar_rect, 4), QBrush(Colors.GREEN))
            # the $ rebate at the row's right edge, GREEN mono.
            p.setFont(f_mono)
            p.setPen(Colors.GREEN)
            p.drawText(QRectF(bar_left, ry, (w - pad) - bar_left, row_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       f"-${r.rebate:,.2f}")
            # the cached-token count + per-model hit-rate, faint under the name.
            sub = f"{_fmt_tok_count(r.cached_tokens)} cached · {r.hit_rate_pct:.0f}% hit"
            faint = QColor(Colors.TEXT_MUTED)
            p.setFont(f_tiny)
            p.setPen(QPen(faint))
            # draw the sub-line just below the bar baseline (compact second line)
            p.drawText(QRectF(bar_left, bar_cy + 2, bar_max_w, fm_tiny.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       sub)
            y += row_h

        # 3) footer — the realized-credit clarifier.
        y += 2
        foot = QColor(Colors.GREEN)
        p.setFont(f_tiny)
        p.setPen(QPen(foot))
        p.drawText(QRectF(pad, y, w - 2 * pad, fm_tiny.height() + 4),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                   "Realized credit, already applied to your balance.")


def _rounded(rect: QRectF, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path


def build_rebate_html(savings) -> str:
    """The per-model rebate dossier for the ProviderPopup: a header + the painted
    breakdown pixmap embedded as a data-URI <img> (single-QLabel contract). Every
    API-sourced string (model short-names) is html.escape'd before it enters the
    HTML wrapper (decision D); the pixmap text itself is QPainter-drawn so it's
    injection-safe by construction. Returns '' when there's nothing to show."""
    if savings is None or savings.is_empty:
        return ("<div style='font-size:9.5pt;color:#a0a0c8;font-weight:bold;'>"
                "— NO CACHE REBATE IN RANGE —</div>")
    # html.escape the names even though they only appear in the (safe) pixmap —
    # belt-and-suspenders per decision D (any name that reaches the wrapper).
    top_name = html.escape(savings.models[0].short_name) if savings.models else ""
    out = [f"<div style='font-size:11pt;font-weight:bold;color:#2ed573;'>"
           f"Caching rebated you ${savings.total_rebate:,.2f}</div>"]
    out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
               f"{savings.hit_rate_pct:.1f}% cache hit · 7-day · ground truth"
               f"{(' · top: ' + top_name) if top_name else ''}</div>")
    try:
        strip = RebateBreakdownStrip(savings)
        pm = strip.render_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        out.append(
            f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
            f"width='{RebateBreakdownStrip.STRIP_W}' height='{strip._h}'></div>")
    except Exception:
        log.debug("rebate strip render failed", exc_info=True)
    out.append("<div style='margin-top:2px;color:#64648c;font-size:8pt;'>"
               "live from openrouter.ai · analytics · refreshed every ~15 min</div>")
    return "".join(out)


def rebate_accent_hex(savings) -> str:
    """The popup border accent for the rebate dossier — always GREEN (the
    savings role this widget owns exclusively in the zone). Returns a #rrggbb."""
    return Colors.GREEN.name()


# ===========================================================================
#  #13 THE SÉANCE — the ghost-model veil
# ===========================================================================
# A full-width "veil" strip: still-active (model,provider) pairs glow as solid
# sigils ABOVE a membrane hairline; pairs that VANISHED week-over-week fade as
# hollow chips sinking BELOW it; pairs that APPEARED this week flare in from the
# right with a one-shot materialize ring + a "new" pip. ALWAYS meaningful — even
# at zero ghosts it renders the living roster + a calm caption. Cross-references
# the Spectrum's per-model colors (spend_palette.model_color — decision D).

# A small fixed palette for the provider tick-dot (the corner pip on each chip),
# keyed by a stable hash of the provider name (NOT the model color, so the dot
# reads as a second channel). Never crimson/green (those are reserved roles).
_PROVIDER_DOT_PALETTE = [
    QColor(0, 210, 255),     # cyan
    QColor(255, 199, 0),     # yellow
    QColor(155, 89, 255),    # purple
    QColor(0, 200, 160),     # teal
    QColor(255, 145, 77),    # soft orange (NOT the alarm red)
    QColor(120, 170, 255),   # periwinkle
]


def _provider_dot_color(provider: str) -> QColor:
    if not provider:
        return QColor(Colors.TEXT_MUTED)
    import hashlib as _hl
    idx = int(_hl.md5(provider.encode("utf-8")).hexdigest()[:8], 16) \
        % len(_PROVIDER_DOT_PALETTE)
    return QColor(_PROVIDER_DOT_PALETTE[idx])


def ghost_accent_hex(entry) -> str:
    """The popup border accent for a séance-ledger dossier — the chip's MODEL
    color (the shared spectrum band, decision D), NEVER crimson. Returns #rrggbb.
    """
    try:
        return spend_palette.model_color(entry.pair.model_id, entry.rank).name()
    except Exception:
        return theme_controller.accent().name()


class GhostVeil(QWidget):
    """THE SÉANCE (#13): a full-width veil strip below the rebate stub. A membrane
    hairline runs across the vertical centre; LIVING (model,provider) pairs glow
    as solid sigils ABOVE it in their shared #9 spectrum colors; VANISHED pairs
    sink BELOW it as hollow desaturated chips with a fading-dot trail + a
    'last seen Nd' caption (never crimson — a vanish is not an error); APPEARED
    pairs flare in ABOVE with a one-shot materialize RING + a 'new' pip (a
    runaway never-before-seen model literally FLARES in at the edge).

    Always alive — at zero ghosts it renders the living roster + a calm caption,
    and on a young account (only one week of data) the calm 'watching — needs a
    2nd full week' caption (decision A/F). Click a glyph -> the SÉANCE LEDGER
    popup (a two-bar last-week/this-week timeline + figures + the re-route note).

    ONE measure pass (_measure) feeds BOTH paint and setFixedHeight (chip wrap is
    measured here so chips never clip). Paint is allocation-light: glyph rects +
    apparition rings are measured in set_data and cached; paint only strokes the
    cached objects. The materialize flair is ONE held QPropertyAnimation on a
    distinct `materialize` Property (NOT a QWidget builtin), started ONCE when
    APPEARED>0 (a static ring when animations are off).
    """

    ghost_clicked = Signal(tuple, QPointF)   # ((model,provider), global_anchor)

    # -- geometry constants (the measure pass positions content WITHIN these) --
    TOP_PAD = 8
    BOTTOM_PAD = 8
    PAD_X = 12
    CHIP_PAD_X = 6          # horizontal text padding inside a chip (×2 = +12)
    CHIP_VPAD = 6           # chip height = label height + this
    CHIP_GAP = 6            # gap between chips on a row
    ROW_GAP = 4             # gap between wrapped chip rows
    CAPTION_GAP = 2         # gap between a chip row and its caption
    # gap region around the veil tuned so the populated height lands at 94 and
    # the calm/young height at 62 (with the deterministic offscreen font metrics).
    VEIL_GAP_POP = 22       # populated: the living-block→veil→vanished-block band
    VEIL_GAP_CALM = 18      # calm/young: living-block → veil (no lower lane)
    CHIP_ELIDE_W = 84       # max chip label width before elide (spec)
    DOT_R = 2.0             # provider tick-dot radius
    RING_PAD = 4.0          # apparition ring sits this far outside the chip

    def __init__(self, parent=None):
        super().__init__(parent)
        self._diff = None          # GhostDiff | None
        self._locked = False
        self._signature = None
        self._materialize = 1.0    # 0..1 apparition ring factor (animated ONCE)
        self._materialize_started_count = 0
        self._caption_text = ""
        # Cached paint geometry (rebuilt in set_data/resize — never alloc in paint)
        self._veil_y = 0.0
        # _glyph_rects: list[(QRectF, (model,provider), role)] for hit-testing.
        self._glyph_rects: list = []
        # _entry_map: {(model,provider): GhostEntry} cached for the paint loop so
        # paint needs no per-chip scan/allocation (the hot-path discipline).
        self._entry_map: dict = {}
        # _apparition_rings: list[QRectF] (chip rects to draw a materialize ring on)
        self._apparition_rings: list = []
        self._hover_key = None

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # ONE held animation on a DISTINCT Property name (NOT pos/size/geometry).
        self._anim = QPropertyAnimation(self, b"materialize")
        self._anim.setDuration(1400)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._measure()
        theme_controller.changed.connect(self._on_theme_changed)

    # -- the materialize Property (distinct name; NOT a QWidget builtin) -----
    def get_materialize(self):
        return self._materialize

    def set_materialize(self, v):
        self._materialize = float(v)
        self.update()

    materialize = Property(float, get_materialize, set_materialize)

    def _on_theme_changed(self):
        # colors are re-fetched from spend_palette in set_data; a theme change
        # only needs a repaint of the cached geometry.
        self.update()

    # ------------------------------------------------------------------
    #  Geometry primitives (font-metric-driven)
    # ------------------------------------------------------------------
    @classmethod
    def _chip_h(cls) -> int:
        return QFontMetrics(Fonts.label()).height() + cls.CHIP_VPAD

    @classmethod
    def _caption_h(cls) -> int:
        return QFontMetrics(Fonts.tiny()).height()

    @classmethod
    def _populated_height(cls) -> int:
        # top_pad + living chip + ghost-caption + veil-band + vanished chip +
        # last-seen caption + bottom_pad. (≈94px at the offscreen font metrics.)
        ch = cls._chip_h()
        cap = cls._caption_h()
        return (cls.TOP_PAD + ch + cap + cls.VEIL_GAP_POP + ch + cap
                + cls.BOTTOM_PAD)

    @classmethod
    def _calm_height(cls) -> int:
        # top_pad + living chip + caption + veil-gap + bottom_pad (no lower lane).
        return (cls.TOP_PAD + cls._chip_h() + cls._caption_h()
                + cls.VEIL_GAP_CALM + cls.BOTTOM_PAD)

    def _has_lower_lane(self) -> bool:
        """The lower (vanished) lane only exists in the POPULATED state — i.e.
        a key is present, NOT young, and there is at least one ghost."""
        d = self._diff
        return bool(d is not None and not d.young_history and d.has_ghosts)

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def set_data(self, diff):
        """diff: a GhostDiff (board.ghosts) or None (keep last-good — never blank).
        """
        if diff is None:
            return
        self._locked = False
        self._diff = diff
        sig = self._compute_signature(diff)
        first_or_changed = (sig != self._signature)
        self._signature = sig
        self._measure()            # sets fixed height + rebuilds glyph geometry
        # One-shot materialize ONLY when there are apparitions AND the roster
        # changed (a 15-min identical poll is silent). Static ring otherwise.
        if first_or_changed and diff.appeared:
            self._start_materialize()
        else:
            self._materialize = 1.0
        self.update()

    def set_locked(self):
        """No management key: a dim DASHED hairline, NO chips, a centered padlock
        + the canonical unlock copy (decision F). ZERO fake glyphs/names. Keeps
        the calm height so the section doesn't jump when a key is added."""
        self._locked = True
        self._diff = None
        self._signature = None
        self._materialize = 1.0
        self._glyph_rects = []
        self._apparition_rings = []
        self._caption_text = SPEND_UNLOCK_BASE + " ghost detection"
        self.setFixedHeight(self._calm_height())
        self.update()

    @staticmethod
    def _compute_signature(diff):
        if diff is None:
            return None
        return (
            bool(diff.young_history),
            tuple(e.pair.key for e in diff.living),
            tuple(e.pair.key for e in diff.vanished),
            tuple(e.pair.key for e in diff.appeared),
        )

    # ------------------------------------------------------------------
    #  Materialize animation (ONE held anim; started only when APPEARED>0)
    # ------------------------------------------------------------------
    def _start_materialize(self):
        try:
            import anim
            on = anim.ANIMATIONS_ON
        except Exception:
            on = True
        self._anim.stop()
        if not on or not self.isVisible():
            self._materialize = 1.0    # static ring (purely additive flair)
            return
        self._materialize = 0.0
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._materialize_started_count += 1

    # ------------------------------------------------------------------
    #  Measure pass (drives BOTH paint geometry and setFixedHeight — no clip)
    # ------------------------------------------------------------------
    def _measure(self):
        """ONE pass: pick the height, place the veil, lay out + WRAP the chips
        into _glyph_rects, and record which appeared-chips get a materialize ring.
        Shared by paint and setFixedHeight so nothing clips."""
        d = self._diff
        populated = self._has_lower_lane()
        h = self._populated_height() if populated else self._calm_height()
        self.setFixedHeight(h)

        self._glyph_rects = []
        self._apparition_rings = []
        self._entry_map = {}
        if d is None or self._locked:
            self._veil_y = h / 2.0
            return
        # Cache the entry-by-key map ONCE (paint reads it, never re-scans).
        for e in (list(getattr(d, "living", ()) or [])
                  + list(getattr(d, "vanished", ()) or [])
                  + list(getattr(d, "appeared", ()) or [])):
            self._entry_map[e.pair.key] = e

        ch = self._chip_h()
        cap = self._caption_h()
        # The veil sits below the living block (chip + its caption) by VEIL_GAP/2
        # so the upper lane has room; in the populated state the lower lane
        # mirrors it. living block top:
        living_top = float(self.TOP_PAD)
        if populated:
            # living chip row top .. caption .. (gap) veil (gap) .. vanished row.
            self._veil_y = (self.TOP_PAD + ch + cap + self.VEIL_GAP_POP / 2.0)
            vanished_top = self._veil_y + self.VEIL_GAP_POP / 2.0
        else:
            self._veil_y = (self.TOP_PAD + ch + cap + self.VEIL_GAP_CALM / 2.0)
            vanished_top = None

        # ABOVE the veil: LIVING then APPEARED (both alive). Appeared last so they
        # flare at the right edge (the spec's 'in from the right').
        above = list(getattr(d, "living", ()) or []) + \
            list(getattr(d, "appeared", ()) or [])
        appeared_keys = {e.pair.key for e in getattr(d, "appeared", ()) or []}
        self._layout_lane(above, living_top, ch, role_above=True,
                          appeared_keys=appeared_keys)

        # BELOW the veil: VANISHED (only in the populated state).
        if populated and vanished_top is not None:
            self._layout_lane(list(getattr(d, "vanished", ()) or []),
                              vanished_top, ch, role_above=False,
                              appeared_keys=appeared_keys)

        # The caption text for the calm/young states (no ghosts / young).
        if d.young_history:
            self._caption_text = "watching — needs a 2nd full week to spot ghosts"
        elif not d.has_ghosts:
            self._caption_text = "no ghosts this week — the veil is still"
        else:
            self._caption_text = ""

    def _chip_w(self, entry) -> float:
        fm = QFontMetrics(Fonts.label())
        label = self._short_label(entry)
        adv = fm.horizontalAdvance(label)
        adv = min(adv, self.CHIP_ELIDE_W)
        return adv + self.CHIP_PAD_X * 2

    def _short_label(self, entry) -> str:
        """A compact chip label: the model short-name with the trailing date
        stamp stripped (claude-4.6-sonnet-20260217 -> claude-4.6-sonnet)."""
        name = entry.pair.short_name or entry.pair.model_id or ""
        # strip a trailing -YYYYMMDD date stamp the OpenRouter ids carry.
        name = _re.sub(r"-\d{6,8}$", "", name)
        return name

    def _layout_lane(self, entries, top, ch, role_above, appeared_keys):
        """Place a lane's chips left->right, WRAPPING to a new row when the inner
        width is exceeded (the measured cumulative advance). Vanished chips sink
        a progressive 2px lower (the spec's sinking cue). Records hit rects +
        apparition ring rects. Allocation happens here (measure), not in paint."""
        if not entries:
            return
        w = max(1, self.width())
        x = float(self.PAD_X)
        y = float(top)
        right_limit = w - self.PAD_X
        sink = 0
        for e in entries:
            cw = self._chip_w(e)
            if x + cw > right_limit and x > self.PAD_X:
                # wrap to the next row within this lane.
                x = float(self.PAD_X)
                y += ch + self.ROW_GAP
            chip_y = y + (sink if not role_above else 0)
            rect = QRectF(x, chip_y, cw, ch)
            is_appeared = e.pair.key in appeared_keys
            role = ("appeared" if (role_above and is_appeared)
                    else "living" if role_above else "vanished")
            self._glyph_rects.append((rect, e.pair.key, role))
            if role == "appeared":
                # the materialize ring is measured here (an ellipse just outside).
                self._apparition_rings.append(rect)
            x += cw + self.CHIP_GAP
            if not role_above:
                sink += 2     # progressive sinking for the departed

    def resizeEvent(self, event):
        self._measure()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    #  Paint (allocation-light: cached rects + cached strokes)
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_veil(p)
        if self._locked:
            self._paint_locked(p)
        else:
            self._paint_populated(p)
        p.end()

    def _paint_veil(self, p):
        """The membrane hairline at the vertical centre: a QLinearGradient that's
        transparent at both ends and the panel accent (~alpha 90) in the middle,
        with a faint blurred echo line below. LOCKED -> a dim DASHED accent line.
        """
        w = self.width()
        y = self._veil_y
        accent = theme_controller.accent()
        if self._locked:
            dim = QColor(accent); dim.setAlpha(40)
            pen = QPen(dim, 1, Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.drawLine(QPointF(self.PAD_X, y), QPointF(w - self.PAD_X, y))
            return
        grad = QLinearGradient(self.PAD_X, 0, w - self.PAD_X, 0)
        edge = QColor(accent); edge.setAlpha(0)
        mid = QColor(accent); mid.setAlpha(90)
        grad.setColorAt(0.0, edge)
        grad.setColorAt(0.5, mid)
        grad.setColorAt(1.0, edge)
        p.setPen(QPen(QBrush(grad), 1))
        p.drawLine(QPointF(self.PAD_X, y), QPointF(w - self.PAD_X, y))
        # a faint echo line just below (reads as a membrane depth).
        echo = QLinearGradient(self.PAD_X, 0, w - self.PAD_X, 0)
        e_edge = QColor(accent); e_edge.setAlpha(0)
        e_mid = QColor(accent); e_mid.setAlpha(30)
        echo.setColorAt(0.0, e_edge)
        echo.setColorAt(0.5, e_mid)
        echo.setColorAt(1.0, e_edge)
        p.setPen(QPen(QBrush(echo), 3))
        p.drawLine(QPointF(self.PAD_X, y + 2.5), QPointF(w - self.PAD_X, y + 2.5))

    def _paint_populated(self, p):
        d = self._diff
        if d is None:
            return
        # 1) the chips (living/appeared above; vanished below). Entry lookup is a
        # cached dict (no per-chip scan/allocation in the paint hot path).
        for (rect, key, role) in self._glyph_rects:
            e = self._entry_map.get(key)
            if e is None:
                continue
            if role == "vanished":
                self._paint_vanished_chip(p, rect, e)
            else:
                self._paint_living_chip(p, rect, e, apparition=(role == "appeared"))
        # 2) the apparition materialize rings (one-shot factor self._materialize).
        self._paint_apparition_rings(p)
        # 3) the calm/young caption (centered just under the veil) if present.
        if self._caption_text:
            self._paint_caption(p, self._caption_text)

    def _paint_living_chip(self, p, rect, entry, apparition=False):
        """A filled rounded-rect chip in the model's SHARED color, BG-dark label,
        a provider tick-dot at the top-left corner. Apparitions also get a 'new'
        pip at the top-right (the ring is drawn separately over the materialize
        factor)."""
        color = spend_palette.model_color(entry.pair.model_id, entry.rank)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        p.fillPath(path, QBrush(color))
        # label (elided), in the dark panel color for contrast on the bright chip.
        label = self._short_label(entry)
        fm = QFontMetrics(Fonts.label())
        label = fm.elidedText(label, Qt.TextElideMode.ElideRight,
                              int(rect.width() - self.CHIP_PAD_X * 2))
        p.setFont(Fonts.label())
        p.setPen(QPen(Colors.BG_DARK))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        # provider tick-dot (top-left corner).
        dot = _provider_dot_color(entry.pair.provider)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(dot))
        p.drawEllipse(QPointF(rect.left() + 3.0, rect.top() + 3.0),
                      self.DOT_R, self.DOT_R)
        if apparition:
            # the accent 'new' pip (top-right).
            pip = theme_controller.accent()
            p.setBrush(QBrush(pip))
            p.drawEllipse(QPointF(rect.right() - 3.0, rect.top() + 3.0), 3.0, 3.0)

    def _paint_vanished_chip(self, p, rect, entry):
        """A hollow desaturated chip: the SAME model color @18 fill / @110 outline,
        TEXT_MUTED label, a 3-dot fading trail ABOVE it, and a 'last seen Nd'
        caption below. NEVER crimson (a vanish is not an error)."""
        base = spend_palette.model_color(entry.pair.model_id, entry.rank)
        fill = QColor(base); fill.setAlpha(18)
        outline = QColor(base); outline.setAlpha(110)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        p.fillPath(path, QBrush(fill))
        p.setPen(QPen(outline, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        # label, muted.
        label = self._short_label(entry)
        fm = QFontMetrics(Fonts.label())
        label = fm.elidedText(label, Qt.TextElideMode.ElideRight,
                              int(rect.width() - self.CHIP_PAD_X * 2))
        p.setFont(Fonts.label())
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        # the 3-dot fading trail ABOVE the chip (alpha 90/50/20) — a 'rising
        # ghost' cue pointing back up at the veil it sank through.
        p.setPen(Qt.PenStyle.NoPen)
        cx = rect.center().x()
        for i, alpha in enumerate((90, 50, 20)):
            d = QColor(base); d.setAlpha(alpha)
            p.setBrush(QBrush(d))
            ty = rect.top() - 3.0 - i * 3.5
            p.drawEllipse(QPointF(cx, ty), 1.4, 1.4)
        # 'last seen Nd' caption under the chip (tiny, muted).
        days = self._last_seen_days(entry)
        cap = f"last seen {days}d" if days is not None else "last seen"
        f_tiny = Fonts.tiny()
        fmt = QFontMetrics(f_tiny)
        p.setFont(f_tiny)
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(QRectF(rect.left() - 8, rect.bottom() + 1,
                          rect.width() + 16, fmt.height()),
                   Qt.AlignmentFlag.AlignHCenter, cap)

    def _last_seen_days(self, entry):
        """Days since the prior-week bucket date (best-effort; None if unknown)."""
        pair = getattr(entry, "prior", None) or entry.pair
        bucket = getattr(pair, "bucket", "") or ""
        if not bucket:
            return None
        try:
            import datetime
            d = datetime.date.fromisoformat(bucket)
            return max(0, (datetime.date.today() - d).days)
        except Exception:
            return None

    def _paint_apparition_rings(self, p):
        """A 2px accent ellipse just outside each apparition chip. The radius
        grows (chip edge -> +RING_PAD) and the alpha fades (200->0) over the
        one-shot `materialize` factor; at factor 1.0 (resolved / anim-off-static)
        a faint resting ring remains as the 'newly arrived' marker."""
        if not self._apparition_rings:
            return
        f = max(0.0, min(1.0, self._materialize))
        accent = theme_controller.accent()
        for rect in self._apparition_rings:
            grow = self.RING_PAD * f
            ring = QRectF(rect.left() - grow, rect.top() - grow,
                          rect.width() + 2 * grow, rect.height() + 2 * grow)
            # alpha 200 -> a resting 70 (never fully gone, so the marker persists).
            alpha = int(200 - (200 - 70) * f)
            col = QColor(accent); col.setAlpha(alpha)
            p.setPen(QPen(col, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(ring, 6, 6)

    def _paint_caption(self, p, text):
        """A single centered TEXT_MUTED caption just below the veil (the calm /
        young state messaging)."""
        f_tiny = Fonts.tiny()
        fm = QFontMetrics(f_tiny)
        y = self._veil_y + 4
        p.setFont(f_tiny)
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(QRectF(self.PAD_X, y, self.width() - 2 * self.PAD_X,
                          fm.height() + 2),
                   Qt.AlignmentFlag.AlignHCenter, text)

    def _paint_locked(self, p):
        """A centered padlock + the canonical unlock copy under the dim dashed
        hairline (the veil is drawn dashed by _paint_veil). NO chips, ZERO fake
        names (decision F)."""
        msg = self._caption_text or (SPEND_UNLOCK_BASE + " ghost detection")
        f_body = Fonts.body()
        fm = QFontMetrics(f_body)
        msg_w = fm.horizontalAdvance(msg)
        avail = self.width() - 2 * self.PAD_X - 22
        msg = fm.elidedText(msg, Qt.TextElideMode.ElideRight, int(avail))
        msg_w = min(msg_w, int(avail))
        cx = self.width() / 2.0
        cy = self._veil_y + 4 + fm.height() / 2.0
        _paint_padlock(p, cx - msg_w / 2.0 - 12, cy, 13, Colors.TEXT_MUTED)
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.setFont(f_body)
        p.drawText(QRectF(self.PAD_X, cy - fm.height() / 2.0,
                          self.width() - 2 * self.PAD_X, fm.height()),
                   Qt.AlignmentFlag.AlignHCenter, msg)

    # ------------------------------------------------------------------
    #  Interaction — click a glyph -> the séance-ledger popup
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if self._locked or self._diff is None:
            super().mousePressEvent(event)
            return
        try:
            pos = event.position()
        except AttributeError:
            pos = QPointF(event.pos())
        for (rect, key, _role) in self._glyph_rects:
            if rect.contains(pos):
                gpos = event.globalPosition() if hasattr(event, "globalPosition") \
                    else QPointF(self.mapToGlobal(event.pos()))
                self.ghost_clicked.emit(key, gpos)
                return
        super().mousePressEvent(event)


class SeanceLedgerStrip(QWidget):
    """The per-pair SÉANCE LEDGER (the #13 click-through), rendered to a QPixmap
    and embedded as a data-URI <img> in the shared ProviderPopup (UptimeStrip
    idiom). A two-bar last-week/this-week mini-timeline of the pair's presence
    (request_count) + the $ + the first/last-seen line; for an apparition the
    'materialized this week — never seen before' line, and for a same-model-new-
    provider move the re-route note.

    All names are QPainter-drawn here (injection-safe); the HTML wrapper
    html.escapes them. devicePixelRatio-aware so text stays crisp on HiDPI."""

    STRIP_W = 292

    def __init__(self, entry, diff, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._diff = diff
        self._h = self._measure_height()
        self.setFixedSize(self.STRIP_W, self._h)

    def _measure_height(self) -> int:
        fm = QFontMetrics(Fonts.tiny())
        # header + the 2-bar timeline (two rows) + figure line + note line(s).
        rows = 2                      # last-week + this-week bars
        notes = 2                     # first/last-seen + (apparition/reroute)
        return int(10 + fm.height() + 6 + rows * 20 + 6 + notes * (fm.height() + 4)
                   + 10)

    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        pm = QPixmap(int(self.STRIP_W * dpr), int(self._h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        e = self._entry
        w = self.STRIP_W
        pad = 12
        color = spend_palette.model_color(e.pair.model_id, e.rank)
        f_tiny = Fonts.tiny()
        f_mono = Fonts.mono_small()
        fm = QFontMetrics(f_tiny)
        y = 10.0

        # header — the provider line (model name is the popup title in HTML).
        p.setFont(f_tiny)
        p.setPen(QPen(Colors.TEXT_SECONDARY))
        p.drawText(QRectF(pad, y, w - 2 * pad, fm.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"via {e.pair.provider or '—'}")
        y += fm.height() + 6

        # two-bar timeline: last week (prior) vs this week (this), bar ∝ requests.
        prior = getattr(e, "prior", None)
        this = getattr(e, "this", None)
        pr = prior.request_count if prior else 0
        tr = this.request_count if this else 0
        ref = max(pr, tr, 1)
        bar_left = pad + 64.0
        bar_max = (w - pad) - bar_left - 44
        for (lbl, reqs, present, is_this) in (
            ("last wk", pr, prior is not None, False),
            ("this wk", tr, this is not None, True),
        ):
            row_cy = y + 10
            p.setFont(f_tiny)
            p.setPen(QPen(Colors.TEXT_MUTED))
            p.drawText(QRectF(pad, y, 60, 20),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       lbl)
            frac = (reqs / ref) if ref > 0 else 0.0
            bw = max(2.0, bar_max * frac) if present else 0.0
            track = QColor(Colors.TEXT_MUTED); track.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.fillPath(_rounded(QRectF(bar_left, row_cy - 4, bar_max, 8), 4),
                       QBrush(track))
            if present and bw > 0:
                # this-week bar is the live model color; last-week is desaturated.
                bc = QColor(color)
                if not is_this:
                    bc.setAlpha(120)
                p.fillPath(_rounded(QRectF(bar_left, row_cy - 4, bw, 8), 4),
                           QBrush(bc))
            # request count at the right.
            p.setFont(f_mono)
            p.setPen(QPen(Colors.TEXT_SECONDARY if present else Colors.TEXT_MUTED))
            txt = f"{reqs}" if present else "—"
            p.drawText(QRectF(bar_left, row_cy - 10, bar_max + 44, 20),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       txt)
            y += 20

        y += 6
        # the $ figure line (the surviving window's usage).
        figure_pair = this or prior
        usage = figure_pair.usage if figure_pair else 0.0
        reqs = figure_pair.request_count if figure_pair else 0
        p.setFont(f_tiny)
        p.setPen(QPen(color))
        verb = "drained" if (this is None) else "spent"
        p.drawText(QRectF(pad, y, w - 2 * pad, fm.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{verb} ${usage:,.4f} over {reqs} requests")
        y += fm.height() + 4

        # the status note: apparition / vanished / re-route / living.
        note = _seance_note_text(e)
        p.setFont(f_tiny)
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(QRectF(pad, y, w - 2 * pad, fm.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   note)


def _seance_role(entry) -> str:
    """'appeared' / 'vanished' / 'living' for an entry (which window it carries).
    """
    has_this = getattr(entry, "this", None) is not None
    has_prior = getattr(entry, "prior", None) is not None
    if has_this and not has_prior:
        return "appeared"
    if has_prior and not has_this:
        return "vanished"
    return "living"


def _seance_note_text(entry) -> str:
    """The plain-text ledger status note (QPainter-safe; the HTML wrapper escapes
    the model name where it interpolates one). Honors the re-route note."""
    role = _seance_role(entry)
    if role == "appeared":
        if getattr(entry, "reroute", False):
            return "same model, new provider — a benign re-route"
        return "materialized this week — never seen before"
    if role == "vanished":
        if getattr(entry, "reroute", False):
            return "same model, new provider — a benign re-route"
        return "gone this week — last week's ghost"
    return "still active — present both weeks"


def build_seance_html(entry, diff) -> str:
    """The per-pair séance-ledger dossier for the ProviderPopup: a header (the
    model + provider, html.escaped — decision E) + the painted timeline pixmap
    embedded as a data-URI <img>. The pixmap text is QPainter-drawn (injection-
    safe); we ALSO html.escape every name that reaches this HTML wrapper. Returns
    a 'no ledger' card when entry is None."""
    if entry is None:
        return ("<div style='font-size:9.5pt;color:#a0a0c8;font-weight:bold;'>"
                "— NO LEDGER ON FILE —</div>")
    model = html.escape(getattr(entry.pair, "short_name", "")
                        or getattr(entry.pair, "model_id", ""))
    provider = html.escape(getattr(entry.pair, "provider", "") or "—")
    role = _seance_role(entry)
    accent = ghost_accent_hex(entry)
    accent = _safe_color(accent, "#a0a0c8")
    title_word = {"appeared": "Materialized", "vanished": "Departed",
                  "living": "Present"}.get(role, "Present")
    out = [f"<div style='font-size:11pt;font-weight:bold;color:{accent};'>"
           f"{title_word}: {model}</div>"]
    note = html.escape(_seance_note_text(entry))
    out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
               f"via {provider} · {note}</div>")
    try:
        strip = SeanceLedgerStrip(entry, diff)
        pm = strip.render_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        out.append(
            f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
            f"width='{SeanceLedgerStrip.STRIP_W}' height='{strip._h}'></div>")
    except Exception:
        log.debug("seance ledger render failed", exc_info=True)
    out.append("<div style='margin-top:2px;color:#64648c;font-size:8pt;'>"
               "live from openrouter.ai · analytics · refreshed every ~15 min</div>")
    return "".join(out)


# ===========================================================================
#  #11 THE AUTOPSY — the spend cause-of-death dossier (the lasso-release sheet)
# ===========================================================================
# A forensic cut-sheet rendered to a QPixmap + embedded as a data-URI <img> in
# the shared ProviderPopup (UptimeStrip idiom). One crimson "incision" bar per
# (model,provider) drained the lassoed window, descending by $, each filled to
# its share of the spike with a CRIMSON->accent lerp (dominant deepest crimson),
# so "93% from ONE model @ ONE provider" is unmissable. Rows beyond 6 collapse
# into a bounded "+N more" remainder bar. The empty window is a single muted
# "clean window" bar (no crimson — a real populated-zero, not the locked state).
class AutopsyStripWidget(QWidget):
    """#11 dossier strip (mirrors UptimeStripWidget/SeanceLedgerStrip: STRIP_W=292,
    render_pixmap()+_paint_into(p), measure-before-allocate so nothing clips).

    All text is QPainter-drawn here (injection-safe); the HTML wrapper ALSO
    html.escapes every model/provider name. devicePixelRatio-aware for crisp
    HiDPI text."""

    STRIP_W = 292
    PAD = 8
    BAR_H = 18
    BAR_GAP = 6
    TRACK_H = 8
    CRIMSON = QColor(224, 70, 60)   # the dominant drain (a "wound"); dossier-only

    def __init__(self, report, parent=None):
        super().__init__(parent)
        self._report = report
        self._row_count = self._rows_drawn()
        self._h = self._measure_height()
        self.setFixedSize(self.STRIP_W, self._h)
        # Test introspection: per drawn row -> (fill_w, track_w, is_remainder).
        self._bar_geom = []

    def _rows_drawn(self) -> int:
        """How many bars the strip paints: visible rows (+1 for a remainder bar),
        or exactly 1 for the empty 'clean window' state."""
        r = self._report
        if r is None or r.is_empty:
            return 1
        return len(r.visible) + (1 if r.remainder_count > 0 else 0)

    def _measure_height(self) -> int:
        # PAD*2 + rows*(BAR_H+BAR_GAP) - BAR_GAP  (the GEOMETRY_PLAN formula).
        rows = max(1, self._row_count)
        return int(self.PAD * 2 + rows * (self.BAR_H + self.BAR_GAP)
                   - self.BAR_GAP)

    def _value_col_w(self) -> int:
        # Reserve a fixed value column wide enough for "$9999.99 · 100%".
        return QFontMetrics(Fonts.mono_small()).horizontalAdvance(
            "$9999.99 · 100%") + 8

    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        pm = QPixmap(int(self.STRIP_W * dpr), int(self._h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _row_color(self, rank: int, n: int) -> QColor:
        """Dominant row = deepest CRIMSON; minor rows lerp toward the panel accent
        so 'one model drained it' reads as a wound while the tail cools off."""
        if n <= 1:
            return QColor(self.CRIMSON)
        t = rank / (n - 1)
        return _lerp_color(self.CRIMSON, theme_controller.accent(), t)

    def _paint_into(self, p):
        w = self.STRIP_W
        pad = self.PAD
        self._bar_geom = []
        r = self._report
        f_body = Fonts.body()
        f_mono = Fonts.mono_small()
        fm_body = QFontMetrics(f_body)
        fm_mono = QFontMetrics(f_mono)
        val_w = self._value_col_w()
        track_left = pad
        track_w = w - 2 * pad
        label_avail = max(10, track_w - val_w - 6)

        # EMPTY window -> a single muted "clean window" bar (no crimson).
        if r is None or r.is_empty:
            y = pad
            cy = y + self.BAR_H / 2.0
            track = QColor(Colors.TEXT_MUTED); track.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.fillPath(_rounded(QRectF(track_left, cy - self.TRACK_H / 2.0,
                                       track_w, self.TRACK_H), 4), QBrush(track))
            self._bar_geom.append((0.0, float(track_w), False))
            label = "No spend — clean window"
            if r is not None:
                label = f"No spend in {r.window_label} — clean window"
            p.setFont(f_body)
            p.setPen(QPen(Colors.TEXT_MUTED))
            p.drawText(QRectF(track_left, y, track_w, self.BAR_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm_body.elidedText(label, Qt.TextElideMode.ElideRight,
                                          int(track_w)))
            return

        n = len(r.visible) + (1 if r.remainder_count > 0 else 0)
        spike = r.spike_total if r.spike_total > 0 else 1.0
        y = pad
        for rank, row in enumerate(r.visible):
            cy = y + self.BAR_H / 2.0
            color = self._row_color(rank, n)
            # 1) the full-width track.
            track = QColor(Colors.TEXT_MUTED); track.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.fillPath(_rounded(QRectF(track_left, cy - self.TRACK_H / 2.0,
                                       track_w, self.TRACK_H), 4), QBrush(track))
            # 2) the crimson incision filled to (usage/spike_total).
            frac = max(0.0, min(1.0, row.usage / spike))
            fill_w = max(2.0, track_w * frac) if frac > 0 else 0.0
            if fill_w > 0:
                p.fillPath(_rounded(QRectF(track_left, cy - self.TRACK_H / 2.0,
                                           fill_w, self.TRACK_H), 4),
                           QBrush(color))
            self._bar_geom.append((float(fill_w), float(track_w), False))
            # 3) left label "model · provider" (elided), over the track.
            label = f"{row.short_name} · {row.provider}"
            p.setFont(f_body)
            p.setPen(QPen(Colors.TEXT_PRIMARY if rank == 0
                          else Colors.TEXT_SECONDARY))
            p.drawText(QRectF(track_left + 2, y, label_avail, self.BAR_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm_body.elidedText(label, Qt.TextElideMode.ElideRight,
                                          int(label_avail)))
            # 4) right value col "$X.XX · NN%".
            val = f"${row.usage:,.2f} · {round(row.share * 100)}%"
            p.setFont(f_mono)
            p.setPen(QPen(Colors.TEXT_PRIMARY if rank == 0
                          else Colors.TEXT_SECONDARY))
            p.drawText(QRectF(w - pad - val_w, y, val_w, self.BAR_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       fm_mono.elidedText(val, Qt.TextElideMode.ElideRight,
                                          int(val_w)))
            y += self.BAR_H + self.BAR_GAP

        # the bounded "+N more · $Y" remainder bar (TEXT_MUTED, never crimson).
        if r.remainder_count > 0:
            cy = y + self.BAR_H / 2.0
            track = QColor(Colors.TEXT_MUTED); track.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.fillPath(_rounded(QRectF(track_left, cy - self.TRACK_H / 2.0,
                                       track_w, self.TRACK_H), 4), QBrush(track))
            frac = max(0.0, min(1.0, r.remainder_usage / spike))
            fill_w = max(2.0, track_w * frac) if frac > 0 else 0.0
            mut = QColor(Colors.TEXT_MUTED); mut.setAlpha(150)
            if fill_w > 0:
                p.fillPath(_rounded(QRectF(track_left, cy - self.TRACK_H / 2.0,
                                           fill_w, self.TRACK_H), 4), QBrush(mut))
            self._bar_geom.append((float(fill_w), float(track_w), True))
            label = f"+{r.remainder_count} more"
            val = f"${r.remainder_usage:,.2f}"
            p.setFont(f_body)
            p.setPen(QPen(Colors.TEXT_MUTED))
            p.drawText(QRectF(track_left + 2, y, label_avail, self.BAR_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       label)
            p.setFont(f_mono)
            p.drawText(QRectF(w - pad - val_w, y, val_w, self.BAR_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       val)


def autopsy_accent_hex(report) -> str:
    """The popup border accent for the autopsy dossier — always CRIMSON (the
    forensic/alarm role), so the frame reads distinct from the cyan info popups
    and the green rebate. Returns a #rrggbb."""
    return AutopsyStripWidget.CRIMSON.name()


def build_autopsy_html(report) -> str:
    """The forensic dossier for the ProviderPopup: an HTML header
    'AUTOPSY · HH:00–HH:00 · $Z drained' + the painted incision pixmap (data-URI
    <img>) + a footer 'N reqs · M cached tokens' and, when a cache offset exists,
    a GREEN 'caching offset −$X here' line (an offset, NEVER a drain — decision
    D). EVERY API name reaching the HTML is html.escape'd (the pixmap text is
    QPainter-drawn, injection-safe by construction). Returns a 'no window' card
    when report is None; a tidy 'clean window' header when $0 was drained."""
    crimson = _safe_color(AutopsyStripWidget.CRIMSON.name(), "#e0463c")
    if report is None:
        return ("<div style='font-size:9.5pt;color:#a0a0c8;font-weight:bold;'>"
                "— NO AUTOPSY ON FILE —</div>")
    window = html.escape(report.window_label)
    out = []
    if report.is_empty:
        out.append(f"<div style='font-size:11pt;font-weight:bold;color:{crimson};'>"
                   f"AUTOPSY · {window}</div>")
        out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
                   "clean window · ground truth</div>")
    else:
        top = report.rows[0]
        top_name = html.escape(f"{top.short_name} · {top.provider}")
        out.append(f"<div style='font-size:11pt;font-weight:bold;color:{crimson};'>"
                   f"AUTOPSY · {window} · ${report.spike_total:,.2f} drained</div>")
        out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
                   f"top suspect: {top_name} · {round(top.share * 100)}% of the "
                   f"spike{' · truncated' if report.truncated else ''}</div>")
    try:
        strip = AutopsyStripWidget(report)
        pm = strip.render_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        out.append(
            f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
            f"width='{AutopsyStripWidget.STRIP_W}' height='{strip._h}'></div>")
    except Exception:
        log.debug("autopsy strip render failed", exc_info=True)
    # FOOTER: request + cached-token totals, then the GREEN caching-offset line.
    if not report.is_empty:
        out.append("<div style='color:#a0a0c8;font-size:8pt;'>"
                   f"{report.request_total:,} reqs · "
                   f"{_fmt_tok(report.cached_total)} cached tokens</div>")
        if report.cache_offset > 0:
            out.append("<div style='color:#2ed573;font-size:8pt;'>"
                       f"caching offset −${report.cache_offset:,.2f} here</div>")
    out.append("<div style='margin-top:2px;color:#64648c;font-size:8pt;'>"
               "live from openrouter.ai · analytics · drag the chart to drill</div>")
    return "".join(out)


# ===========================================================================
#  #14 THE HOURGLASS — the sand-clock budget burn-down (CLOSES the Spend zone)
# ===========================================================================
# A wide ~84px callout whose TOP bulb is remaining budget and drained BOTTOM is
# spend, cross-faded against a diagonal PACE tick marking where the sand SHOULD
# be given % of the period elapsed. The pinch reddens when AHEAD OF PACE (before
# 100%). Rhymes UPWARD with the balance ArcGauge to bookend the panel. Degrades
# to a tidy "Set a budget" state (the live default — all /budgets routes 404).


def _hourglass_path(cx: float, top: float, bulb_w: float, bulb_h: float,
                    pinch_w: float, gap: float):
    """Build the two glass-bulb trapezoids (TOP + BOTTOM) sharing a `gap`-tall
    pinch at the vertical centre, centred on cx. Returns (top_path, bottom_path,
    pinch_y_top, pinch_y_bot) — pure given inputs. Each bulb is a trapezoid: a
    wide outer edge tapering to the pinch_w throat."""
    half_w = bulb_w / 2.0
    half_p = pinch_w / 2.0
    pinch_y_top = top + bulb_h          # throat top (bottom of the upper bulb)
    pinch_y_bot = pinch_y_top + gap     # throat bottom (top of the lower bulb)

    top_path = QPainterPath()
    top_path.moveTo(cx - half_w, top)
    top_path.lineTo(cx + half_w, top)
    top_path.lineTo(cx + half_p, pinch_y_top)
    top_path.lineTo(cx - half_p, pinch_y_top)
    top_path.closeSubpath()

    bot_path = QPainterPath()
    bot_path.moveTo(cx - half_p, pinch_y_bot)
    bot_path.lineTo(cx + half_p, pinch_y_bot)
    bot_path.lineTo(cx + half_w, pinch_y_bot + bulb_h)
    bot_path.lineTo(cx - half_w, pinch_y_bot + bulb_h)
    bot_path.closeSubpath()
    return top_path, bot_path, pinch_y_top, pinch_y_bot


class BudgetHourglass(QWidget):
    """THE HOURGLASS (#14): a wide ~84px callout that CLOSES the Spend section.
    A hand-painted hourglass — the TOP bulb is remaining budget, the drained
    BOTTOM bulb is spend — raced against a diagonal PACE tick on the glass's
    right edge marking where the sand SHOULD be given % of the period elapsed.
    One glance shows whether you're burning faster than the clock; the pinch
    glows RED when you're AHEAD OF PACE (before you hit 100%). It rhymes UPWARD
    with the balance ArcGauge to bookend the panel.

    Beats a progress bar: a bar shows HOW MUCH but not WHETHER YOU'RE ON TRACK
    (82% is fine on day 6/7, a disaster on day 2/7). The Hourglass encodes TIME
    twice (sand drained + pace tick) and the projection row makes the forecast
    explicit. Click -> a large hourglass + a 7-bar daily-spend column chart with
    the pace line + the projection math.

    STATES (decision E, ZERO fake numbers): POPULATED (a real denominator) = the
    full glass + 3 rows; NO-BUDGET (key present, no weekly_budget, credits opt-in
    off — the live default) = a dashed top-bulb outline + a "Set a budget" pill;
    LOCKED (no mgmt key) = an empty padlocked glass + the unlock copy.

    ONE measure pass feeds BOTH paint and setFixedHeight (font-metric-driven, no
    clip). Paint is allocation-light (glass paths measured in set_data, cached;
    paint strokes the cached objects). The fill is a ONE-TIME reveal on a held
    QPropertyAnimation on a DISTINCT `display_frac` Property (NOT a QWidget
    builtin), re-fired only on a >0.5% change (static when animations are off).
    """

    budget_clicked = Signal(QPointF)

    # -- geometry constants (the measure pass derives everything from these) --
    # PAD=12 so top/bottom 12 + the 60px glass lands the fixed height at ~84px
    # (the spec's GEOMETRY_PLAN target, matching the BurnRateBar card rhythm it
    # replaces) and the diagonal pace tick below the bottom bulb never clips.
    PAD = 12
    GLYPH_W = 70           # left column width (the glass lives here)
    BULB_W = 46.0          # widest bulb edge
    PINCH_W = 3.0          # the throat width (a 3px pinch)
    PINCH_GAP = 4.0        # vertical gap the throat spans
    TEXT_X = 78            # right text column left edge
    ROW_GAP = 4
    REVEAL_MS = 800

    def __init__(self, parent=None):
        super().__init__(parent)
        self._budget = None        # Budget | None
        self._locked = False
        self._no_budget = False
        self._signature = None
        self._display_frac = 0.0   # 0..spent_frac, animated ONCE
        self._target_frac = 0.0
        self._reveal_started_count = 0

        # Cached paint geometry (rebuilt in set_data/resize — never alloc in paint)
        self._top_path = None
        self._bot_path = None
        self._pinch_y_top = 0.0
        self._pinch_y_bot = 0.0
        self._glass_top = 0.0
        self._glass_cx = 0.0
        self._bulb_h = 28.0
        # the 3 text rows (label strings precomputed in set_data, not in paint).
        self._row1 = ""        # "$X / $Y"
        self._row2 = ""        # "{pct}% burned · {n} days left"
        self._row3 = ""        # "on pace for $Z by reset"
        self._caption = ""     # locked/no-budget message

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # ONE held animation on a DISTINCT Property name (NOT pos/size/geometry).
        self._anim = QPropertyAnimation(self, b"display_frac")
        self._anim.setDuration(self.REVEAL_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._measure()
        theme_controller.changed.connect(self.update)

    # -- the display_frac Property (distinct name; NOT a QWidget builtin) -----
    def get_display_frac(self):
        return self._display_frac

    def set_display_frac(self, v):
        self._display_frac = float(v)
        self.update()

    display_frac = Property(float, get_display_frac, set_display_frac)

    # ------------------------------------------------------------------
    #  Geometry primitives (font-metric-driven; ONE measure pass)
    # ------------------------------------------------------------------
    @classmethod
    def _text_block_h(cls) -> int:
        return (QFontMetrics(Fonts.mono_medium()).height()
                + QFontMetrics(Fonts.body()).height()
                + QFontMetrics(Fonts.tiny()).height()
                + 2 * cls.ROW_GAP)

    @classmethod
    def _glass_h(cls) -> float:
        # two 28px bulbs + the throat gap (≈60px).
        return 2 * 28.0 + cls.PINCH_GAP

    @classmethod
    def _fixed_height(cls) -> int:
        # top/bottom pad + max(glass, text-block). Lands ~84px at the offscreen
        # font metrics (matching the BurnRateBar card rhythm it replaces).
        return int(cls.PAD * 2 + max(cls._glass_h(), cls._text_block_h()))

    def _measure(self):
        """ONE pass: pick the fixed height, place + cache the glass paths, and
        precompute the 3 text-row strings. Shared by paint and setFixedHeight so
        nothing clips. Allocation happens here, never in paint."""
        h = self._fixed_height()
        self.setFixedHeight(h)
        self._bulb_h = 28.0
        glass_h = self._glass_h()
        # vertically centre the glass within the padded content box.
        self._glass_top = (h - glass_h) / 2.0
        self._glass_cx = self.PAD + self.GLYPH_W / 2.0
        self._top_path, self._bot_path, self._pinch_y_top, self._pinch_y_bot = \
            _hourglass_path(self._glass_cx, self._glass_top, self.BULB_W,
                            self._bulb_h, self.PINCH_W, self.PINCH_GAP)
        self._compute_rows()

    def _compute_rows(self):
        """Precompute the 3 text rows from the current Budget (decision E — no
        fabricated numbers; the no-budget/locked captions carry no denominator).
        """
        b = self._budget
        if self._locked:
            self._row1 = self._row2 = self._row3 = ""
            self._caption = SPEND_UNLOCK_BASE + " track a budget"
            return
        if self._no_budget or b is None or not b.has_budget:
            self._row1 = self._row2 = self._row3 = ""
            self._caption = "Set a budget"
            return
        self._caption = ""
        self._row1 = f"${b.spent:,.2f} / ${b.budget:,.0f}"
        self._row2 = f"{b.pct_burned}% burned · {b.days_left} days left"
        self._row3 = f"on pace for ${b.projection:,.2f} by reset"

    def resizeEvent(self, event):
        self._measure()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def set_data(self, budget):
        """budget: a Budget (board.budget) or None (keep last-good — never blank).
        Routes to the no-budget state when the Budget carries no denominator
        (decision A: never invent one)."""
        if budget is None:
            return
        # A Budget with no denominator IS the live "Set a budget" state.
        if not getattr(budget, "has_budget", False):
            self.set_no_budget(budget)
            return
        self._locked = False
        self._no_budget = False
        self._budget = budget
        sig = (budget.source, round(budget.spent_frac, 5),
               round(budget.elapsed_frac, 5), budget.days_left,
               round(budget.projection, 4))
        first_or_changed = (sig != self._signature)
        self._signature = sig
        self._target_frac = budget.spent_frac
        self._measure()
        # One-time reveal ONLY when the value changed by >0.5% (a 15-min identical
        # poll is silent). Static (set final) when animations are off.
        if first_or_changed and abs(self._target_frac - self._display_frac) > 0.005:
            self._start_reveal()
        else:
            self._display_frac = self._target_frac
        self.update()

    def set_no_budget(self, budget=None):
        """Key present but NO denominator (weekly_budget==0 + credits opt-in off
        — the live default). A faint dashed top-bulb outline + a "Set a budget"
        pill. NO fabricated denominator (decision A/E)."""
        self._locked = False
        self._no_budget = True
        self._budget = budget        # may carry a real period-to-date spent $
        self._signature = ("none",)
        self._display_frac = 0.0
        self._target_frac = 0.0
        self._measure()
        self.update()

    def set_locked(self):
        """No management key: an empty glass in flat BORDER outline, the pinch
        crossed with a padlock, the unlock copy (decision E). NO denominator
        invented. Keeps the real fixed height so the section doesn't jump."""
        self._locked = True
        self._no_budget = False
        self._budget = None
        self._signature = None
        self._display_frac = 0.0
        self._target_frac = 0.0
        self._measure()
        self.update()

    def _start_reveal(self):
        try:
            import anim
            on = anim.ANIMATIONS_ON
        except Exception:
            on = True
        self._anim.stop()
        if not on or not self.isVisible():
            self._display_frac = self._target_frac   # final directly (static)
            return
        self._anim.setStartValue(self._display_frac)
        self._anim.setEndValue(self._target_frac)
        self._anim.start()
        self._reveal_started_count += 1

    # ------------------------------------------------------------------
    #  Paint (allocation-light: cached paths + cached strokes)
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._locked:
            self._paint_glass_outline(p, dashed=False)
            self._paint_padlock_pinch(p)
            self._paint_caption(p, self._caption, muted=True)
        elif self._no_budget:
            self._paint_glass_outline(p, dashed=True)
            self._paint_set_budget_pill(p)
        else:
            self._paint_populated(p)
        p.end()

    def _paint_glass_outline(self, p, dashed: bool):
        """The empty glass: both bulb trapezoids stroked in BORDER (DotLine when
        dashed for the no-budget teaser). Shared by the locked + no-budget paths.
        """
        style = Qt.PenStyle.DotLine if dashed else Qt.PenStyle.SolidLine
        pen = QPen(Colors.BORDER, 1.5, style)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._top_path is not None:
            p.drawPath(self._top_path)
            p.drawPath(self._bot_path)

    def _paint_padlock_pinch(self, p):
        """A tiny padlock crossing the pinch (the locked glass)."""
        cy = (self._pinch_y_top + self._pinch_y_bot) / 2.0
        _paint_padlock(p, self._glass_cx, cy, 13, Colors.TEXT_MUTED)

    def _paint_set_budget_pill(self, p):
        """A 'Set a budget' pill painted in the text column (no fabricated $)."""
        f = Fonts.body()
        fm = QFontMetrics(f)
        label = self._caption or "Set a budget"
        tw = fm.horizontalAdvance(label)
        pill_h = fm.height() + 8
        pill_w = tw + 22
        x = self.TEXT_X
        y = (self.height() - pill_h) / 2.0 - 4
        accent = theme_controller.accent()
        rect = QRectF(x, y, min(pill_w, self.width() - x - self.PAD), pill_h)
        # a dashed accent-tinted pill (a call to action, not an alarm).
        outline = QColor(accent); outline.setAlpha(150)
        p.setPen(QPen(outline, 1.2, Qt.PenStyle.DashLine))
        fill = QColor(accent); fill.setAlpha(22)
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(rect, pill_h / 2.0, pill_h / 2.0)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(accent)))
        p.setFont(f)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        # a faint subcaption under the pill so the empty state reads intentional.
        sub_f = Fonts.tiny()
        p.setFont(sub_f)
        p.setPen(QPen(Colors.TEXT_MUTED))
        sfm = QFontMetrics(sub_f)
        p.drawText(QRectF(x, rect.bottom() + 1,
                          self.width() - x - self.PAD, sfm.height() + 2),
                   Qt.AlignmentFlag.AlignLeft,
                   "set weekly_budget to track burn-down")

    def _paint_caption(self, p, text, muted=True):
        """A left-aligned caption in the text column (the locked unlock copy)."""
        f = Fonts.body()
        fm = QFontMetrics(f)
        x = self.TEXT_X
        avail = self.width() - x - self.PAD
        msg = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(max(10, avail)))
        p.setPen(QPen(Colors.TEXT_MUTED if muted else Colors.TEXT_SECONDARY))
        p.setFont(f)
        p.drawText(QRectF(x, 0, avail, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, msg)

    def _paint_populated(self, p):
        """The full hourglass: top sand fill (remaining), bottom spend hump, the
        falling-grain bridge, the PACE tick + dotted leader, the pinch/crest glow
        (accent on-track / RED over-pace), and the 3 text rows."""
        b = self._budget
        if b is None or self._top_path is None:
            return
        frac = max(0.0, min(1.0, self._display_frac))     # animated spent_frac
        remaining_frac = 1.0 - frac
        accent = theme_controller.accent()
        over = b.over_pace

        # --- glass outline first (sand fills clip to the bulb paths) ----------
        p.setPen(QPen(Colors.BORDER, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._top_path)
        p.drawPath(self._bot_path)

        cx = self._glass_cx
        half_w = self.BULB_W / 2.0

        # --- TOP bulb: remaining budget, filled from the pinch UPWARD ---------
        # a flat accent-tinted sand fill whose TOP edge is a static sine ripple.
        if remaining_frac > 0.001:
            p.save()
            p.setClipPath(self._top_path)
            fill_h = self._bulb_h * remaining_frac
            fill_top = self._pinch_y_top - fill_h
            sand = QColor(accent); sand.setAlpha(140)
            # body of the sand below the ripple line.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(sand))
            p.drawRect(QRectF(cx - half_w, fill_top + 2,
                              self.BULB_W, fill_h))
            # a 2px-amplitude static sine ripple along the top edge (one polyline).
            ripple = QPolygonF()
            steps = 16
            for i in range(steps + 1):
                t = i / steps
                rx = (cx - half_w) + t * self.BULB_W
                ry = fill_top + 2 + math.sin(t * math.pi * 3) * 2.0
                ripple.append(QPointF(rx, ry))
            crest = QColor(accent); crest.setAlpha(200)
            p.setPen(QPen(crest, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(ripple)
            p.restore()

        # --- BOTTOM bulb: spent, a settled gradient HUMP from the pinch DOWN ---
        if frac > 0.001:
            p.save()
            p.setClipPath(self._bot_path)
            mound_h = self._bulb_h * frac
            base_y = self._pinch_y_bot + self._bulb_h     # bottom of the bulb
            peak_y = base_y - mound_h
            # accent -> severity gradient (warms toward severity as budget drains,
            # mirroring BurnRateBar's idiom — credit_color(remaining_frac)).
            grad = QLinearGradient(0, peak_y, 0, base_y)
            grad.setColorAt(0.0, QColor(accent))
            warm = QColor(Colors.credit_color(remaining_frac))
            grad.setColorAt(1.0, warm)
            # a quadratic hump: taller in the middle (a settled pile of sand).
            hump = QPainterPath()
            hump.moveTo(cx - half_w, base_y)
            hump.lineTo(cx - half_w, peak_y + 4)
            hump.quadTo(cx, peak_y - 4, cx + half_w, peak_y + 4)
            hump.lineTo(cx + half_w, base_y)
            hump.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPath(hump)
            # the crest line of the mound glows RED when over-pace, else accent.
            crest_col = Colors.RED if over else QColor(accent)
            cc = QColor(crest_col); cc.setAlpha(220)
            crest = QPainterPath()
            crest.moveTo(cx - half_w, peak_y + 4)
            crest.quadTo(cx, peak_y - 4, cx + half_w, peak_y + 4)
            p.setPen(QPen(cc, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(crest)
            p.restore()

        # --- the falling-grain line bridges the pinch when burning is live ----
        if 0.001 < frac < 0.999:
            grain = QColor(accent); grain.setAlpha(200)
            p.setPen(QPen(grain, self.PINCH_W * 0.7))
            p.drawLine(QPointF(cx, self._pinch_y_top - 2),
                       QPointF(cx, self._pinch_y_bot + 2))

        # --- the pinch glow (accent on-track / RED over-pace) -----------------
        pinch_glow = Colors.RED if over else QColor(accent)
        pg = QColor(pinch_glow); pg.setAlpha(230)
        p.setPen(QPen(pg, 2.2))
        p.drawLine(QPointF(cx - self.PINCH_W, self._pinch_y_top),
                   QPointF(cx + self.PINCH_W, self._pinch_y_top))
        p.drawLine(QPointF(cx - self.PINCH_W, self._pinch_y_bot),
                   QPointF(cx + self.PINCH_W, self._pinch_y_bot))

        # --- THE PACE TICK: a diagonal dash on the OUTSIDE-right edge of the ---
        # glass at the height where sand SHOULD be (elapsed_frac into the bottom
        # 'should-spent' bulb), + a hairline dotted leader across the glass.
        base_y = self._pinch_y_bot + self._bulb_h
        pace_y = base_y - (self._bulb_h * max(0.0, min(1.0, b.elapsed_frac)))
        edge_x = cx + half_w
        tick_col = Colors.TEXT_ACCENT
        # dotted leader across the glass at the pace height.
        leader = QColor(tick_col); leader.setAlpha(120)
        p.setPen(QPen(leader, 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(cx - half_w, pace_y), QPointF(edge_x, pace_y))
        # the 10px diagonal accent dash just outside the right edge.
        p.setPen(QPen(tick_col, 2))
        p.drawLine(QPointF(edge_x + 2, pace_y + 4),
                   QPointF(edge_x + 9, pace_y - 4))

        # --- the 3 text rows in the right column ------------------------------
        x = self.TEXT_X
        avail = self.width() - x - self.PAD
        y = float(self.PAD)
        # row1: "$X / $Y" (spent / budget), mono.
        f1 = Fonts.mono_medium()
        fm1 = QFontMetrics(f1)
        p.setFont(f1)
        p.setPen(QPen(Colors.TEXT_PRIMARY))
        p.drawText(QRectF(x, y, avail, fm1.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._row1)
        y += fm1.height() + self.ROW_GAP
        # row2: "{pct}% burned · {n} days left", body. RED tint when over-pace.
        f2 = Fonts.body()
        fm2 = QFontMetrics(f2)
        p.setFont(f2)
        p.setPen(QPen(Colors.RED if over else Colors.TEXT_SECONDARY))
        row2 = self._row2 + ("   ▲ ahead of pace" if over else "")
        row2 = fm2.elidedText(row2, Qt.TextElideMode.ElideRight, int(avail))
        p.drawText(QRectF(x, y, avail, fm2.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   row2)
        y += fm2.height() + self.ROW_GAP
        # row3: projection, tiny muted — RED when the forecast overshoots budget.
        f3 = Fonts.tiny()
        fm3 = QFontMetrics(f3)
        p.setFont(f3)
        p.setPen(QPen(Colors.RED if b.over_projection else Colors.TEXT_MUTED))
        p.drawText(QRectF(x, y, avail, fm3.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._row3)

    # ------------------------------------------------------------------
    #  Interaction — click -> the budget burn-down popup
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        # the locked glass has no dossier; no-budget + populated do.
        if self._locked:
            super().mousePressEvent(event)
            return
        gpos = event.globalPosition() if hasattr(event, "globalPosition") \
            else QPointF(self.mapToGlobal(event.pos()))
        self.budget_clicked.emit(gpos)


class BudgetBurndownStrip(QWidget):
    """The #14 click-through dossier, rendered to a QPixmap and embedded as a
    data-URI <img> in the shared ProviderPopup (the UptimeStrip idiom). A 7-bar
    daily-spend column chart with the avg-daily pace line overlaid + the
    projection math 'avg $a/day × b days left + $spent = $Z'.

    All text is QPainter-drawn (injection-safe); the HTML wrapper has no API
    strings here (numbers only). devicePixelRatio-aware for crisp HiDPI text."""

    STRIP_W = 300

    def __init__(self, budget, parent=None):
        super().__init__(parent)
        self._b = budget
        self._h = self._measure_height()
        self.setFixedSize(self.STRIP_W, self._h)

    def _measure_height(self) -> int:
        fm = QFontMetrics(Fonts.tiny())
        # the column chart block + 3 figure/math lines.
        return int(12 + 64 + 12 + 3 * (fm.height() + 4) + 10)

    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        pm = QPixmap(int(self.STRIP_W * dpr), int(self._h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        b = self._b
        w = self.STRIP_W
        pad = 12
        accent = theme_controller.accent()
        f_tiny = Fonts.tiny()
        f_mono = Fonts.mono_small()
        fm = QFontMetrics(f_tiny)
        chart_top = 12.0
        chart_h = 64.0
        chart_left = float(pad)
        chart_right = float(w - pad)
        chart_w = chart_right - chart_left

        daily = list(getattr(b, "daily", ()) or [])[-7:]
        n = len(daily)
        max_v = max((v for (_, v) in daily), default=0.0)
        base_y = chart_top + chart_h

        # baseline.
        p.setPen(QPen(Colors.BORDER, 1))
        p.drawLine(QPointF(chart_left, base_y), QPointF(chart_right, base_y))

        # the daily spend columns (accent).
        if n > 0:
            slot = chart_w / n
            bar_w = min(slot * 0.6, 26.0)
            for i, (label, v) in enumerate(daily):
                cx = chart_left + slot * (i + 0.5)
                frac = (v / max_v) if max_v > 0 else 0.0
                bh = max(1.0, chart_h * frac)
                rect = QRectF(cx - bar_w / 2, base_y - bh, bar_w, bh)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(accent)))
                p.drawRoundedRect(rect, 2, 2)

        # the avg-daily PACE line (dashed) — spikes above it read as burning ahead.
        if max_v > 0 and getattr(b, "avg_daily", 0.0) > 0:
            avg_frac = min(1.0, b.avg_daily / max_v)
            avg_y = base_y - chart_h * avg_frac
            pace = QColor(Colors.TEXT_ACCENT); pace.setAlpha(170)
            p.setPen(QPen(pace, 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(chart_left, avg_y), QPointF(chart_right, avg_y))
            p.setFont(f_tiny)
            p.setPen(QPen(pace))
            p.drawText(QRectF(chart_left, avg_y - fm.height() - 1, chart_w,
                              fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"avg ${b.avg_daily:,.2f}/day")

        y = base_y + 12
        # figure line 1: spent / budget + pct.
        p.setFont(f_mono)
        p.setPen(QPen(Colors.TEXT_PRIMARY))
        p.drawText(QRectF(pad, y, w - 2 * pad, fm.height() + 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"${b.spent:,.2f} / ${b.budget:,.0f}   {b.pct_burned}% burned")
        y += fm.height() + 4
        # figure line 2: elapsed / days-left + on/ahead of pace.
        over = bool(getattr(b, "over_pace", False))
        p.setFont(f_tiny)
        p.setPen(QPen(Colors.RED if over else Colors.TEXT_SECONDARY))
        pace_word = "AHEAD of pace" if over else "on pace"
        p.drawText(QRectF(pad, y, w - 2 * pad, fm.height() + 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"day {b.elapsed_days}/{b.period_days} · {b.days_left} left "
                   f"· {pace_word}")
        y += fm.height() + 4
        # the projection MATH line (the explicit forecast).
        p.setPen(QPen(Colors.RED if getattr(b, "over_projection", False)
                      else Colors.TEXT_MUTED))
        p.drawText(QRectF(pad, y, w - 2 * pad, fm.height() + 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"${b.avg_daily:,.2f}/day × {b.days_left}d + ${b.spent:,.2f} "
                   f"= ${b.projection:,.2f}")


def budget_accent_hex(budget) -> str:
    """The popup border accent for the burn-down dossier: RED when over-pace
    (the zone's single 'in trouble' signal, decision C), else the panel accent.
    Returns a #rrggbb hex."""
    if budget is not None and getattr(budget, "over_pace", False):
        return Colors.RED.name()
    return theme_controller.accent().name()


def build_budget_html(budget) -> str:
    """The burn-down dossier for the ProviderPopup: a header + the painted column
    chart + projection math embedded as a data-URI <img> (single-QLabel
    contract). There are NO API strings here (numbers only). Returns a 'No budget
    set' card when there's no denominator (decision E — no fabricated numbers)."""
    if budget is None or not getattr(budget, "has_budget", False):
        return ("<div style='font-size:11pt;font-weight:bold;color:#a0a0c8;'>"
                "No budget set</div>"
                "<div style='color:#64648c;font-size:8pt;margin-top:4px;'>"
                "Set <b>weekly_budget</b> in settings (or enable "
                "<b>show_credit_burndown</b>) to track a burn-down. "
                "Pulse never invents a number.</div>")
    over = bool(getattr(budget, "over_pace", False))
    accent = _safe_color(budget_accent_hex(budget), "#a0a0c8")
    src_word = {"weekly": "Weekly budget",
                "credits": "Credit burn-down"}.get(budget.source, "Budget")
    head = (f"AHEAD of pace — ${budget.projection:,.2f} forecast" if over
            else f"on pace for ${budget.projection:,.2f}")
    out = [f"<div style='font-size:11pt;font-weight:bold;color:{accent};'>"
           f"{html.escape(src_word)}: {budget.pct_burned}% burned</div>"]
    out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
               f"{head}</div>")
    try:
        strip = BudgetBurndownStrip(budget)
        pm = strip.render_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        out.append(
            f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
            f"width='{BudgetBurndownStrip.STRIP_W}' height='{strip._h}'></div>")
    except Exception:
        log.debug("budget burndown render failed", exc_info=True)
    out.append("<div style='margin-top:2px;color:#64648c;font-size:8pt;'>"
               "live from openrouter.ai · analytics · refreshed every ~15 min</div>")
    return "".join(out)


# ===========================================================================
#  #15 THE ASSAY — the struck-coin value standard (Insights zone anchor)
# ===========================================================================
# Each pinned model is a hand-painted struck COIN whose DIAMETER = quality-per-
# dollar (an AA index ÷ the cheapest prompt $/Mtok), ranged on a LOG-scaled milled
# rail from BASE -> STERLING. The top-value coin is GOLD and wears a struck '✦
# STANDARD' hallmark with the '×' multiple engraved above it; copper/silver fill
# the rest by value-RANK. The model's Spend hue (spend_palette.model_color) is
# ONLY a 2px rim keyline (metal = "how good a deal"; rim = "which model"). One
# board-level widget (NOT per-card). Pure compute over value_assay.AssayResult;
# zero new network. Tap a coin -> a 3-category assay certificate popup.
#
# House contract: ONE font-metric measure pass (in set_data) feeds BOTH paint and
# sizeHint; pens/metal brushes + coin geometry are built in set_data, never the
# paint hot path; ONE held QPropertyAnimation drives a DISTINCT `_strike` Property
# (never pos/size) so the coins strike into existence without the widget moving.

# The metal lane (value-rank fills) — two-stop vertical sheens per the spec.
_METAL_GOLD = (QColor(0xE8, 0xC4, 0x6A), QColor(0xC9, 0xA0, 0x3A))    # sterling/top
_METAL_SILVER = (QColor(0xC9, 0xCD, 0xD6), QColor(0x9A, 0xA0, 0xAD))  # mid
_METAL_COPPER = (QColor(0xC0, 0x7A, 0x4E), QColor(0x9A, 0x5A, 0x36))  # low
_GOLD_INK = QColor(0xE8, 0xC4, 0x6A)   # hallmark/× accent on dark; ink-shifted on gold

# ---- #16 THE TITLE BELT — the CHAMPIONSHIP GOLD + dark LEATHER lane ---------
# A lane no other Insights widget owns: a dark leather strap with stitching, a
# gold escutcheon + side-plates, and ink engraving. Gold stays the belt identity;
# the champion's spend hue appears ONLY as a thin keyline around the center plate.
_BELT_STRAP_TOP = QColor(0x2A, 0x21, 0x18)     # leather strap gradient top
_BELT_STRAP_BOT = QColor(0x1C, 0x16, 0x0F)     # leather strap gradient bottom
_BELT_STITCH = QColor(0x6B, 0x55, 0x33)        # the dashed stitching thread
_BELT_PLATE_TOP = QColor(0xF4, 0xC9, 0x5D)     # gold plate gradient top
_BELT_PLATE_BOT = QColor(0xB8, 0x86, 0x0B)     # gold plate gradient bottom
_BELT_PLATE_RIM = QColor(0x8A, 0x6D, 0x1F)     # the 2px plate rim
_BELT_ENGRAVE = QColor(0x1A, 0x12, 0x05)       # engraved ink on gold (primary)
_BELT_ENGRAVE_MUTE = QColor(0x4A, 0x3A, 0x12)  # engraved ink on gold (muted)
_BELT_LOCKED_STRAP = QColor(0x24, 0x24, 0x2C)  # ghosted (keyless) strap
_BELT_LOCKED_PLATE = QColor(0x3A, 0x3A, 0x46)  # ghosted (keyless) plate


def _metal_for_rank(rank: int, n_assayable: int):
    """The two-stop sheen for a value rank: 0 -> gold; the worst -> copper; the
    middle band(s) -> silver. With 2 coins it's gold (best) + copper (worst)."""
    if rank <= 0:
        return _METAL_GOLD
    if n_assayable >= 2 and rank >= n_assayable - 1:
        return _METAL_COPPER
    return _METAL_SILVER


class ValueAssayWidget(QWidget):
    """#15 THE ASSAY (board-level). set_data(AssayResult) paints the milled rail
    + a struck coin per pinned model sized by log-value, the gold STANDARD coin's
    hallmark + '×' multiple, hollow 'unassayable' coins (decision C), and the
    0-pin / 1-pin degrade states (decision D). Clicking a coin emits
    coin_clicked(model_id, anchor_y_global) -> the dashboard's assay-certificate
    dossier. Clicking the right-hand metric label cycles intelligence->coding->
    agentic (metric_cycled(next_metric))."""

    coin_clicked = Signal(str, int)        # (model_id, global anchor y)
    metric_cycled = Signal(str)            # the next metric to compute/show

    # -- geometry constants (the measure pass derives everything from these) --
    RAIL_BAND_H = 76                       # fits 58px coin + the × engraving + hallmark
    COIN_MIN_D = 34.0
    COIN_SPAN = 24.0                       # d(v) = 34 + 24*logScale(v)  -> 34..58
    SINGLE_D = 46.0                        # 1-pin / equal-value centred coin
    CAP_PAD = 8                            # gap after the BASE/STERLING cap labels

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None                # value_assay.AssayResult | None
        self._metric = "agentic"           # active metric (default agentic)
        self._strike = 1.0                 # 0..1 coin-strike factor (animated once)
        self._signature = None             # one-time strike gate
        self._metric_hit = None            # QRectF of the clickable metric label
        # Cached from the measure pass (rebuilt in set_data/resize) — the paint
        # hot path only reads these, never recomputes:
        self._caption_h = 0
        self._coin_geom = []               # list[dict]: per-coin paint + hit data
        self._rail_rect = None             # QRectF | None
        # Pre-built pens/brushes (allocation-free paint):
        self._rail_track_pen = QPen(Colors.BORDER, 3, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap)
        self._mill_pen = QPen(Colors.TEXT_MUTED, 1)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # ONE held animation (ArcGauge/Spectrum idiom) — never per-frame alloc.
        self._anim = QPropertyAnimation(self, b"strike")
        self._anim.setDuration(600)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._measure()                    # establish an initial fixed height
        theme_controller.changed.connect(self._on_theme_changed)

    # -- the strike Property (DISTINCT name; NOT a QWidget builtin) --
    def get_strike(self):
        return self._strike

    def set_strike(self, v):
        self._strike = float(v)
        self.update()

    strike = Property(float, get_strike, set_strike)

    def _on_theme_changed(self):
        # model_color rims ride the live accent -> rebuild geometry + repaint.
        self._build_geometry()
        self.update()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def set_data(self, result):
        """result: a value_assay.AssayResult (active-metric sorted) or None.

        None => keep last good (the anchor never blanks). On a first/changed
        populated render the coins strike in once; an identical re-distribution
        is silent (no re-mint)."""
        if result is None:
            return
        self._result = result
        self._metric = result.metric
        sig = self._compute_signature(result)
        first_or_changed = (sig != self._signature)
        self._signature = sig
        self._measure()
        self._build_geometry()
        if first_or_changed and not result.is_empty:
            self._start_strike()
        else:
            self._strike = 1.0
        self.update()

    def current_metric(self) -> str:
        return self._metric

    def result_for(self, model_id):
        """The AssayModel for a coin (the dashboard reads it to build the
        certificate). None when absent / no data."""
        if self._result is None:
            return None
        for m in self._result.models:
            if m.model_id == model_id:
                return m
        return None

    # ------------------------------------------------------------------
    #  Strike animation (one-time)
    # ------------------------------------------------------------------
    def _start_strike(self):
        try:
            import anim
            on = anim.ANIMATIONS_ON
        except Exception:
            on = True
        self._anim.stop()
        if not on:                          # reduce-motion -> instant
            self._strike = 1.0
            return
        self._strike = 0.0
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    @staticmethod
    def _compute_signature(result):
        # Active metric + the model set + per-model value rounded — cheap+stable
        # so a 15-min identical re-distribution doesn't re-mint the coins.
        return (
            result.metric,
            tuple((m.model_id, m.unassayable,
                   None if m.value is None else round(m.value, 3))
                  for m in result.models),
        )

    # ------------------------------------------------------------------
    #  Measure pass (drives BOTH paint and setFixedHeight — no clipping)
    # ------------------------------------------------------------------
    def _measure(self):
        # caption_h = tiny height + 6 ; total height = caption_h + 76 + 10.
        self._caption_h = QFontMetrics(Fonts.tiny()).height() + 6
        total_h = self._caption_h + self.RAIL_BAND_H + 10
        self.setFixedHeight(int(total_h))

    def sizeHint(self) -> QSize:
        h = self._caption_h + self.RAIL_BAND_H + 10
        return QSize(280, int(h))

    # ------------------------------------------------------------------
    #  Geometry build (cache rail rect + per-coin geometry).
    #  Runs in set_data/resize — NOT the paint hot path.
    # ------------------------------------------------------------------
    def _content_rect(self):
        return QRectF(0, 0, max(1, self.width()), self.height())

    def _build_geometry(self):
        self._coin_geom = []
        self._rail_rect = None
        cr = self._content_rect()
        res = self._result
        # Rail x-span: leave room for the BASE/STERLING cap labels at both ends.
        cap_w = QFontMetrics(Fonts.tiny()).horizontalAdvance("STERLING") + self.CAP_PAD
        rail_left = cr.left() + cap_w
        rail_right = cr.right() - cap_w
        rail_w = max(20.0, rail_right - rail_left)
        rail_y = self._caption_h + 38.0     # band center
        self._rail_rect = QRectF(rail_left, rail_y, rail_w, 0.0)

        if res is None or res.is_empty:
            return

        assayable = res.assayable
        n_assay = len(assayable)
        values = [m.value for m in assayable if m.value is not None]
        vmin = min(values) if values else 1.0
        vmax = max(values) if values else 1.0
        equal = (not values) or (vmax <= vmin)
        single_centered = (n_assay == 1 and not any(x.unassayable for x in res.models))

        # Draw back-to-front by ASCENDING value so the winner (largest) sits on
        # top if two coins overlap. Hollow (unassayable) coins go first/lowest.
        ordered = (sorted(assayable, key=lambda m: (m.value or 0.0))
                   + [m for m in res.models if m.unassayable])

        from value_assay import log_scale
        for m in ordered:
            if m.unassayable:
                d = self.SINGLE_D
                t = 0.06                    # hollow coins ride near BASE
            elif equal or n_assay == 1:
                d = self.SINGLE_D
                t = 0.5
            else:
                t = log_scale(m.value, vmin, vmax)
                d = self.COIN_MIN_D + self.COIN_SPAN * t
            # Single coin (1 assayable + no hollow) -> dead-centre (decision D).
            if single_centered:
                cx = rail_left + rail_w / 2.0
            else:
                cx = rail_left + rail_w * t
            half = d / 2.0
            cx = max(rail_left + half, min(rail_right - half, cx))
            cy = rail_y
            self._coin_geom.append({
                "model_id": m.model_id,
                "model": m,
                "d": d,
                "cx": cx,
                "cy": cy,
                "rank": m.rank,
                "n_assay": n_assay,
                "is_winner": (m.rank == 0 and n_assay >= 1 and not m.unassayable),
                "hollow": m.unassayable,
            })

    def resizeEvent(self, event):
        self._build_geometry()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    #  Paint
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_caption(p)
        self._paint_rail(p)
        res = self._result
        if res is None or res.is_empty:
            self._paint_empty(p)
        else:
            # winner drawn LAST (on top) — ordered list already ascends by value.
            for g in self._coin_geom:
                self._paint_coin(p, g)
            self._paint_headline_hint(p)
        p.end()

    def _paint_caption(self, p):
        f = Fonts.tiny()
        p.setFont(f)
        p.setPen(QPen(Colors.TEXT_MUTED))
        cr = self._content_rect()
        cap_top = QRectF(cr.left(), 0, cr.width(), self._caption_h)
        p.drawText(cap_top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "VALUE STANDARD · quality per $/Mtok")
        # Right: the active metric label (clickable to cycle). Underlined to
        # signal the affordance; stored for hit-testing in mousePressEvent.
        mf = Fonts.tiny()
        mf.setUnderline(True)
        p.setFont(mf)
        p.setPen(QPen(Colors.TEXT_SECONDARY))
        label = self._metric
        adv = QFontMetrics(mf).horizontalAdvance(label)
        self._metric_hit = QRectF(cr.right() - adv - 2, 0, adv + 4, self._caption_h)
        p.drawText(self._metric_hit,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

    def _paint_rail(self, p):
        rr = self._rail_rect
        if rr is None:
            return
        y = rr.y()
        left = rr.x()
        right = rr.x() + rr.width()
        # The 3px rounded track.
        p.setPen(self._rail_track_pen)
        p.drawLine(QPointF(left, y), QPointF(right, y))
        # 1px vertical milling ticks every ~28px.
        p.setPen(self._mill_pen)
        x = left
        while x <= right:
            p.drawLine(QPointF(x, y - 4), QPointF(x, y + 4))
            x += 28.0
        # BASE / STERLING cap labels.
        p.setFont(Fonts.tiny())
        p.setPen(QPen(Colors.TEXT_MUTED))
        fm = QFontMetrics(Fonts.tiny())
        lab_h = fm.height()
        p.drawText(QRectF(0, y - lab_h / 2.0, left - 2, lab_h),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "BASE")
        p.drawText(QRectF(right + 2, y - lab_h / 2.0,
                          self.width() - right - 2, lab_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "STERLING")

    def _paint_empty(self, p):
        rr = self._rail_rect
        if rr is None:
            return
        p.setFont(Fonts.body())
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(QRectF(0, rr.y() - 12, self.width(), 24),
                   Qt.AlignmentFlag.AlignCenter, "Pin a model to assay its value")

    def _paint_coin(self, p, g):
        d = g["d"] * (self._strike if self._strike > 0 else 0.0)
        if d < 1.0:
            return
        half = d / 2.0
        cx, cy = g["cx"], g["cy"]
        rect = QRectF(cx - half, cy - half, d, d)
        m = g["model"]

        if g["hollow"]:
            # Unassayable: rim-only ring + a struck "no benchmark". No metal.
            rim = spend_palette.model_color(m.model_id, m.spend_rank)
            ring = QColor(rim); ring.setAlpha(150)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 2, Qt.PenStyle.DashLine))
            p.drawEllipse(rect)
            self._draw_struck_text(p, "no", cx, cy - d * 0.10, Fonts.tiny(),
                                   Colors.TEXT_MUTED, light_metal=False)
            self._draw_struck_text(p, "benchmark", cx, cy + d * 0.12, Fonts.tiny(),
                                   Colors.TEXT_MUTED, light_metal=False)
            return

        # Filled metal disc with a two-stop vertical sheen.
        top, bot = _metal_for_rank(g["rank"], g["n_assay"])
        grad = QLinearGradient(cx, cy - half, cx, cy + half)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bot)
        p.setBrush(QBrush(grad))
        # 2px milled RIM = the model's Spend hue (identity keyline).
        rim = spend_palette.model_color(m.model_id, m.spend_rank)
        p.setPen(QPen(rim, 2))
        p.drawEllipse(rect)

        # On gold, ink is dark for contrast; on copper/silver, light primary.
        is_gold = (g["rank"] == 0)
        light_metal = is_gold
        # struck short name across the face (auto-shrunk to fit d-10, then elided).
        name = _coin_short_name(m.display)
        name_font = self._fit_font(name, d - 10)
        name = QFontMetrics(name_font).elidedText(
            name, Qt.TextElideMode.ElideRight, int(max(8.0, d - 10)))
        self._draw_struck_text(p, name, cx, cy - d * 0.08, name_font,
                               Colors.TEXT_PRIMARY, light_metal)
        # value score struck below in mono.
        if m.value is not None:
            vtxt = f"{m.value:.1f}"
            self._draw_struck_text(p, vtxt, cx, cy + d * 0.20, Fonts.mono_small(),
                                   Colors.TEXT_PRIMARY, light_metal)

        # The hallmark on the top-value coin + the engraved × multiple above it.
        if g["is_winner"] and g["n_assay"] >= 2:
            self._paint_hallmark(p, g)

    def _paint_hallmark(self, p, g):
        a = max(0.0, min(1.0, self._strike))    # fade in over the same Property
        if a <= 0.01:
            return
        cx, cy = g["cx"], g["cy"]
        d = g["d"] * (self._strike if self._strike > 0 else 1.0)
        # '✦ STANDARD' notched punch riveted upper-right.
        tab_x = cx + d * 0.32
        tab_y = cy - d * 0.42
        gold = QColor(_GOLD_INK); gold.setAlphaF(a)
        p.setFont(Fonts.tiny())
        p.setPen(QPen(gold))
        p.drawText(QRectF(tab_x - 30, tab_y - 8, 90, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "✦ STANDARD")
        # The faint engraved '4.8×' floating above the coin.
        mult = self._result.top_multiple if self._result else None
        if mult is not None:
            mf = Fonts.mono_small()
            ink = QColor(_GOLD_INK); ink.setAlphaF(a)
            p.setFont(mf)
            p.setPen(QPen(ink))
            p.drawText(QRectF(cx - 40, cy - d * 0.5 - 16, 80, 16),
                       Qt.AlignmentFlag.AlignCenter, f"{mult:.1f}×")

    def _paint_headline_hint(self, p):
        # 1-pin headline (decision D): "<model> · value N.N (pin another to compare)"
        res = self._result
        if res is None:
            return
        assay = res.assayable
        if len(assay) != 1 or any(m.unassayable for m in res.models):
            return
        m = assay[0]
        if m.value is None:
            return
        txt = f"{_coin_short_name(m.display)} · value {m.value:.1f} (pin another to compare)"
        p.setFont(Fonts.tiny())
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(QRectF(0, self.height() - 14, self.width(), 14),
                   Qt.AlignmentFlag.AlignCenter, txt)

    # -- struck-text helper: a 1px stamped shadow (dark drop + light highlight)
    #    so the text reads on light OR dark metal --
    def _draw_struck_text(self, p, text, cx, cy, font, ink, light_metal):
        fm = QFontMetrics(font)
        adv = fm.horizontalAdvance(text)
        h = fm.height()
        base = QRectF(cx - adv / 2.0 - 1, cy - h / 2.0, adv + 2, h)
        p.setFont(font)
        if light_metal:
            shadow = QColor(0, 0, 0, 120); hi = QColor(255, 255, 255, 90)
            ink_c = QColor(40, 30, 10)            # dark ink on gold
        else:
            shadow = QColor(0, 0, 0, 150); hi = QColor(255, 255, 255, 40)
            ink_c = QColor(ink)
        # drop shadow (down-right) + highlight (up-left) + ink.
        p.setPen(QPen(shadow))
        p.drawText(base.translated(0.6, 0.6),
                   Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(QPen(hi))
        p.drawText(base.translated(-0.6, -0.6),
                   Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(QPen(ink_c))
        p.drawText(base, Qt.AlignmentFlag.AlignCenter, text)

    def _fit_font(self, text, max_w):
        """Shrink from Fonts.label() down to Fonts.tiny() until `text` fits
        max_w; the caller elides if even tiny overflows."""
        for f in (Fonts.label(), Fonts.body(), Fonts.tiny()):
            if QFontMetrics(f).horizontalAdvance(text) <= max_w:
                return f
        return Fonts.tiny()

    # ------------------------------------------------------------------
    #  Interaction — coin hit-test + metric cycle
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        # Metric label first (top-right).
        hit = self._metric_hit
        if hit is not None and hit.contains(pos):
            self.metric_cycled.emit(self._next_metric())
            return
        # Coins: front-to-back (winner is last drawn / on top) -> reverse.
        for g in reversed(self._coin_geom):
            half = (g["d"] / 2.0) + 2
            dx = pos.x() - g["cx"]
            dy = pos.y() - g["cy"]
            if dx * dx + dy * dy <= half * half:
                gy = int(self.mapToGlobal(QPoint(0, int(g["cy"]))).y())
                self.coin_clicked.emit(g["model_id"], gy)
                return
        super().mousePressEvent(event)

    def _next_metric(self) -> str:
        from value_assay import METRICS
        try:
            i = METRICS.index(self._metric)
        except ValueError:
            i = METRICS.index("agentic")
        return METRICS[(i + 1) % len(METRICS)]


def _coin_short_name(display: str) -> str:
    """A short coin face label from a model's display name. Strips a 'Vendor: '
    prefix ('Z.ai: GLM 5.2' -> 'GLM 5.2', 'Anthropic: Claude Opus 4.8' ->
    'Claude Opus 4.8'); the face font-fit + elide handle the rest."""
    s = (display or "").strip()
    if ":" in s:
        s = s.split(":", 1)[1].strip()
    return s or display or ""


# ---- #15 assay CERTIFICATE (the tap-through dossier) ----------------------

class AssayCertificateStrip(QWidget):
    """The painted 3-category assay certificate for one model: a header score row
    + three mini value-bars (intelligence / coding / agentic), each a strip with
    its 2-decimal value (and the winner's × multiple vs the field). All text is
    QPainter-drawn (injection-safe); the HTML wrapper ALSO html.escapes names.
    devicePixelRatio-aware. Measure-before-allocate so nothing clips."""

    STRIP_W = 300
    PAD = 10
    ROW_H = 20
    ROW_GAP = 8
    TRACK_H = 9
    LABEL_W = 78

    def __init__(self, model, result, parent=None):
        super().__init__(parent)
        self._m = model                    # value_assay.AssayModel
        self._result = result              # value_assay.AssayResult (for the field max)
        self._h = self._measure_height()
        self.setFixedSize(self.STRIP_W, self._h)

    def _measure_height(self) -> int:
        from value_assay import METRICS
        header_h = QFontMetrics(Fonts.mono_small()).height() + 4
        rows = len(METRICS)
        return int(self.PAD * 2 + header_h + 6
                   + rows * (self.ROW_H + self.ROW_GAP) - self.ROW_GAP)

    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        pm = QPixmap(int(self.STRIP_W * dpr), int(self._h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        from value_assay import METRICS
        w = self.STRIP_W
        pad = self.PAD
        m = self._m
        res = self._result
        # The field max per metric (for bar scaling + the winner's × multiple).
        field_max = {}
        for k in METRICS:
            vals = [mm.value_by_metric.get(k) for mm in (res.models if res else [])]
            vals = [v for v in vals if v is not None]
            field_max[k] = max(vals) if vals else None

        f_head = Fonts.mono_small()
        fm_head = QFontMetrics(f_head)
        # Header: the model name + active-metric value.
        head = _coin_short_name(m.display)
        p.setFont(f_head)
        p.setPen(QPen(Colors.TEXT_PRIMARY))
        p.drawText(QRectF(pad, pad, w - 2 * pad, fm_head.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, head)

        y = pad + fm_head.height() + 6
        track_left = pad + self.LABEL_W
        track_w = w - track_left - pad - 56     # reserve a value column
        f_lab = Fonts.tiny()
        f_val = Fonts.mono_small()
        for k in METRICS:
            cy = y + self.ROW_H / 2.0
            # row label (the AA index name).
            p.setFont(f_lab)
            p.setPen(QPen(Colors.TEXT_SECONDARY))
            p.drawText(QRectF(pad, y, self.LABEL_W - 4, self.ROW_H),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, k)
            # track.
            track = QColor(Colors.TEXT_MUTED); track.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.fillPath(_rounded(QRectF(track_left, cy - self.TRACK_H / 2.0,
                                       track_w, self.TRACK_H), 4), QBrush(track))
            v = m.value_by_metric.get(k)
            fmax = field_max.get(k)
            if v is not None and fmax and fmax > 0:
                frac = max(0.0, min(1.0, v / fmax))
                fill_w = max(2.0, track_w * frac)
                # winner of THIS metric -> gold; else the model's rim hue.
                is_metric_top = (abs(v - fmax) < 1e-9)
                col = _GOLD_INK if is_metric_top else \
                    spend_palette.model_color(m.model_id, m.spend_rank)
                p.fillPath(_rounded(QRectF(track_left, cy - self.TRACK_H / 2.0,
                                           fill_w, self.TRACK_H), 4), QBrush(QColor(col)))
                # value text in the reserved column.
                p.setFont(f_val)
                p.setPen(QPen(Colors.TEXT_PRIMARY))
                p.drawText(QRectF(track_left + track_w + 4, y, 52, self.ROW_H),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           f"{v:.2f}")
            else:
                # No AA index for this metric -> a tidy "—" (NOT a fake bar).
                p.setFont(f_val)
                p.setPen(QPen(Colors.TEXT_MUTED))
                p.drawText(QRectF(track_left + track_w + 4, y, 52, self.ROW_H),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "—")
            y += self.ROW_H + self.ROW_GAP


def build_assay_certificate_html(model, result) -> str:
    """The #15 assay certificate for the ProviderPopup: a header + the painted
    3-bar breakdown embedded as a data-URI <img> (single-QLabel contract) + the
    auditable footnote. Every API-sourced string (model/provider names) is
    html.escape'd before it enters the HTML wrapper (the pixmap text itself is
    QPainter-drawn so it's injection-safe by construction). When the active
    metric's AA index is missing, the footnote shows the LABELLED 'ELO basis'
    fallback (scale-honesty — ELO appears ONLY here, never as a coin diameter).
    Returns '' when there's nothing to show."""
    if model is None:
        return ("<div style='font-size:9.5pt;color:#a0a0c8;font-weight:bold;'>"
                "— NO ASSAY ON FILE —</div>")
    name = html.escape(_coin_short_name(model.display) or model.model_id)
    metric = html.escape(result.metric if result else "agentic")
    out = []
    if model.value is not None:
        # The headline value + (winner) the × multiple vs the field.
        head_extra = ""
        if result is not None and model.rank == 0 and result.top_multiple is not None:
            head_extra = (f" · <span style='color:#E8C46A;'>"
                          f"{result.top_multiple:.1f}× the field</span>")
        out.append(
            f"<div style='font-size:11pt;font-weight:bold;color:#E8C46A;'>"
            f"{name} — {metric} value {model.value:.1f}{head_extra}</div>")
    else:
        out.append(
            f"<div style='font-size:11pt;font-weight:bold;color:#a0a0c8;'>"
            f"{name} — unassayable on {metric}</div>")
    out.append("<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
               "quality-per-dollar · all three AA indices · USER key</div>")
    try:
        strip = AssayCertificateStrip(model, result)
        pm = strip.render_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        out.append(
            f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
            f"width='{AssayCertificateStrip.STRIP_W}' height='{strip._h}'></div>")
    except Exception:
        log.debug("assay certificate render failed", exc_info=True)
    # The auditable footnote: the EXACT denominator the PRICE column shows.
    if model.price is not None:
        prov = html.escape(model.provider or "")
        active_idx = model.quality_by_metric.get(result.metric if result else "agentic")
        idx_txt = (f"AA {active_idx:.1f} (0-100)" if active_idx is not None
                   else "AA n/a")
        prov_txt = (f" @ {prov}" if prov else "")
        out.append(
            "<div style='margin-top:2px;color:#64648c;font-size:8pt;'>"
            f"quality = {idx_txt} · price = cheapest prompt endpoint "
            f"${model.price:.2f}/Mtok{prov_txt}</div>")
    else:
        out.append("<div style='margin-top:2px;color:#64648c;font-size:8pt;'>"
                   "price pending — no priced prompt endpoint yet</div>")
    # ELO basis (scale-honesty) — LABELLED, certificate-only, never on the rail.
    if model.value is None and model.peak_elo is not None:
        out.append(
            "<div style='margin-top:2px;color:#9AA0AD;font-size:8pt;'>"
            f"ELO basis (not value-ranked): peak ELO {int(model.peak_elo)}</div>")
    return "".join(out)


def assay_accent_hex(model) -> str:
    """The popup border accent for the assay certificate: GOLD for the value
    STANDARD (rank 0), else the model's shared Spend hue. Returns a #rrggbb."""
    if model is not None and getattr(model, "rank", -1) == 0:
        return _GOLD_INK.name()
    if model is not None:
        return spend_palette.model_color(model.model_id, model.spend_rank).name()
    return _GOLD_INK.name()


# ===========================================================================
#  #16 THE TITLE BELT — Model of the Week (the second Insights widget)
# ===========================================================================
def _fmt_tokens(n: int) -> str:
    """Compact token magnitude for a side-plate ('6695148' -> '6.70M tok')."""
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B tok"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M tok"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K tok"
    return f"{n} tok"


class ModelOfWeekBelt(QWidget):
    """#16 THE TITLE BELT — the week's CHAMPION engraved on a hand-painted
    championship belt (the second widget in the Insights zone, under #15).

    A dark leather strap (#2A2118 gradient + dashed stitching) carries a gold
    center escutcheon — the champion's provider logo (or a monogram disc when no
    tile is cached, decision D) + the humanized model name + the engraved share
    ('100% OF THIS WEEK') — flanked by two gold side-plates (week spend $ + week
    tokens). Under the escutcheon a challenger ribbon: a muted
    'WEEK 1 · NO PRIOR ROUND' banner while only ONE week bucket exists (the honest
    young-account state — NEVER a fabricated delta), upgrading to a green/red
    momentum cartouche once a 2nd week's share delta is real (decision B).

    set_data(ModelOfWeek) paints the belt; None keeps the last-good champion.
    set_locked() paints a ghosted grey belt + padlock + 'Unlock to crown your
    weekly model'. Clicking the belt emits week_clicked(anchor_y_global) -> the
    dashboard's week-dossier popup.

    Motion: ONE held QPropertyAnimation drives a distinct `glint` Property (NOT a
    QWidget builtin — the widget never moves) sweeping a white-alpha highlight
    across the gold plate ONCE when the champion CHANGES; a 15-min same-champion
    re-poll repaints silently (the gate). Allocation-free paint: pens/brushes and
    the measured geometry are built in set_data, never paintEvent."""

    week_clicked = Signal(int)             # global anchor y for the dossier popup

    # -- geometry constants (the measure pass derives everything from these) --
    STRAP_RADIUS = 9
    STITCH_INSET = 3
    PAD = 8
    SIDE_INSET = 14                        # side-plate x-inset from the strap ends
    LOGO_PX = 26                           # champion logo / monogram diameter

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mow = None                   # model_of_week.ModelOfWeek | None
        self._locked = False
        self._glint = 1.0                  # 0..1 sheen sweep position (animated once)
        self._last_champion = None         # champion-CHANGED gate (None == fresh)
        self._logo_store = None            # shared LogoStore (decision D)
        self._logo_pixmap_cache = None     # cached champion QPixmap (or None)
        self._logo_slug_cached = None      # slug the cache was built for

        # Cached from the measure pass (rebuilt in set_data/resize) — paint reads
        # only these, never recomputes geometry:
        self._strap_h = 0
        self._strap_rect = None            # QRectF | None
        self._plate_rect = None            # center escutcheon bounding rect
        self._plate_path = None            # the shield QPainterPath
        self._left_plate = None            # spend side-plate rect
        self._right_plate = None           # tokens side-plate rect
        self._ribbon_rect = None           # the ribbon / cartouche rect
        self._logo_rect = None             # the logo/monogram disc rect

        # Pre-built strokes (allocation-free paint).
        self._rim_pen = QPen(_BELT_PLATE_RIM, 2)
        self._stitch_pen = QPen(_BELT_STITCH, 1, Qt.PenStyle.DashLine)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # ONE held animation (ArcGauge/Assay idiom) — never per-frame alloc.
        self._anim = QPropertyAnimation(self, b"glint")
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._measure()
        theme_controller.changed.connect(self._on_theme_changed)

    # -- the glint Property (DISTINCT name; NOT a QWidget builtin) --
    def get_glint(self):
        return self._glint

    def set_glint(self, v):
        self._glint = float(v)
        self.update()

    glint = Property(float, get_glint, set_glint)

    def _on_theme_changed(self):
        # the model_color keyline rides the live accent -> rebuild + repaint.
        self._build_geometry()
        self.update()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def set_logo_store(self, store):
        """Wire the shared logo cache (the SAME store the pinned cards use). The
        belt requests the champion's provider logo and uses the cached tile if it
        lands; otherwise it paints a monogram disc (decision D)."""
        self._logo_store = store
        self._logo_pixmap_cache = None
        self._logo_slug_cached = None
        if store is not None and self._mow is not None and not self._mow.is_empty:
            try:
                store.ready.connect(self._on_logo_ready)
            except Exception:
                pass
            self._request_champion_logo()

    def _on_logo_ready(self, slug):
        # a tile we were waiting on cached -> drop the cache so the next paint
        # (or this repaint) picks up the real logo in place of the monogram.
        if self._mow is not None and slug == self._mow.provider:
            self._logo_pixmap_cache = None
            self._logo_slug_cached = None
            self.update()

    def set_data(self, mow):
        """mow: a model_of_week.ModelOfWeek (or None). None => keep last-good (the
        belt never blanks). The glint fires ONCE when the champion CHANGES; an
        identical re-distribution (same champion id) repaints silently."""
        if mow is None:
            return
        self._locked = False
        self._mow = mow
        champ = None if mow.is_empty else mow.champion_id
        changed = (champ is not None and champ != self._last_champion)
        self._last_champion = champ
        # a new champion -> invalidate the logo cache + (re)request its tile.
        self._logo_pixmap_cache = None
        self._logo_slug_cached = None
        self._request_champion_logo()
        self._measure()
        self._build_geometry()
        if changed:
            self._start_glint()
        else:
            self._glint = 1.0
        self.update()

    def set_locked(self):
        """No management key: a ghosted grey belt silhouette + padlock + the
        canonical unlock copy. The locked height MATCHES the populated height so
        the section never jumps when a key is added. ZERO fake champion."""
        self._locked = True
        self._mow = None
        self._last_champion = None
        self._glint = 1.0
        self._measure()
        self._build_geometry()
        self.update()

    # ------------------------------------------------------------------
    #  State predicates (read by paint + the tests)
    # ------------------------------------------------------------------
    def _is_week_one(self) -> bool:
        return self._mow is not None and self._mow.is_week_one

    def _has_momentum(self) -> bool:
        """True when a real 2nd-week delta exists -> the green/red cartouche
        (NEVER on a 1-bucket account)."""
        return (self._mow is not None and not self._mow.is_empty
                and self._mow.wow_delta is not None)

    # ------------------------------------------------------------------
    #  Logo / monogram (decision D)
    # ------------------------------------------------------------------
    def _request_champion_logo(self):
        """Ask the shared store for the champion provider's logo (idempotent).
        The champion usually isn't a pinned model, so this is best-effort — the
        monogram disc is the expected fallback."""
        store = self._logo_store
        mow = self._mow
        if store is None or mow is None or mow.is_empty or not mow.provider:
            return
        # The provider slug == the model-id prefix ('anthropic'); the pinned-card
        # logos are keyed by the same provider slug. We have no icon URL here
        # (the champion isn't a card), so we can only USE a tile already cached
        # by a card — request with an empty URL is a safe no-op in the store.
        try:
            store.request(mow.provider, "")
        except Exception:
            pass

    def _champion_pixmap(self):
        """The cached champion logo QPixmap, or None (-> monogram). Loads the
        tile file from the shared store's cache the first time and memoizes it."""
        mow = self._mow
        store = self._logo_store
        if mow is None or mow.is_empty or store is None or not mow.provider:
            return None
        if self._logo_pixmap_cache is not None and \
                self._logo_slug_cached == mow.provider:
            return self._logo_pixmap_cache
        try:
            path = store.tile_path(mow.provider)
        except Exception:
            path = None
        self._logo_slug_cached = mow.provider
        if not path:
            self._logo_pixmap_cache = None
            return None
        px = QPixmap(path)
        self._logo_pixmap_cache = None if px.isNull() else px
        return self._logo_pixmap_cache

    def _monogram_letter(self) -> str:
        mow = self._mow
        if mow is None or mow.is_empty:
            return "?"
        src = mow.provider or mow.champion_name or mow.champion_id
        return (src[:1] or "?").upper()

    def _champion_accent(self) -> QColor:
        """The champion's shared Spend hue — used ONLY as the thin keyline around
        the center plate (gold stays the belt identity)."""
        mow = self._mow
        if mow is None or mow.is_empty:
            return QColor(_BELT_PLATE_RIM)
        # rank 0 == the champion (heaviest spender this week) -> the panel accent.
        return spend_palette.model_color(mow.champion_id, 0)

    # ------------------------------------------------------------------
    #  Glint animation (one-time, champion-CHANGED gated)
    # ------------------------------------------------------------------
    def _start_glint(self):
        try:
            import anim
            on = anim.ANIMATIONS_ON
        except Exception:
            on = True
        self._anim.stop()
        if not on or not self.isVisible():
            self._glint = 1.0
            return
        self._glint = 0.0
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    # ------------------------------------------------------------------
    #  Measure pass (drives BOTH paint and setFixedHeight — no clipping)
    # ------------------------------------------------------------------
    def _measure(self):
        label_h = QFontMetrics(Fonts.label()).height()
        tiny_h = QFontMetrics(Fonts.tiny()).height()
        ribbon_h = tiny_h + 4
        # strap height fits: logo + name + share engraving + ribbon + paddings.
        strap_h = max(86, self.LOGO_PX + label_h + tiny_h + ribbon_h + 6 * self.PAD)
        self._strap_h = int(strap_h)
        self.setFixedHeight(self._strap_h)

    def sizeHint(self) -> QSize:
        return QSize(320, self._strap_h)

    # ------------------------------------------------------------------
    #  Geometry build (cache strap/plate/side-plate/ribbon rects + the shield
    #  path). Runs in set_data/resize — NOT the paint hot path.
    # ------------------------------------------------------------------
    def _build_geometry(self):
        w = max(1, self.width())
        h = self._strap_h
        self._strap_rect = QRectF(0, 0, w, h)

        # CENTER escutcheon: a gold shield, width clamped to name + engrave-pad.
        name = self._mow.champion_name if (self._mow and not self._mow.is_empty) else ""
        name_w = QFontMetrics(Fonts.label()).horizontalAdvance(name) + 2 * 16
        plate_w = max(140.0, min(name_w, w * 0.5))
        plate_h = h - 12.0
        plate_x = (w - plate_w) / 2.0
        plate_y = 6.0
        self._plate_rect = QRectF(plate_x, plate_y, plate_w, plate_h)
        self._plate_path = self._shield_path(self._plate_rect)

        # The logo/monogram disc, top-center inside the plate.
        lp = float(self.LOGO_PX)
        self._logo_rect = QRectF(plate_x + (plate_w - lp) / 2.0,
                                 plate_y + self.PAD, lp, lp)

        # TWO side-plates: width = '$0.00' advance + pad; centered vertically;
        # x-inset SIDE_INSET from the strap ends.
        sp_w = QFontMetrics(Fonts.mono_small()).horizontalAdvance("$00.00") + 22.0
        sp_h = max(30.0, plate_h * 0.42)
        sp_y = (h - sp_h) / 2.0
        self._left_plate = QRectF(self.SIDE_INSET, sp_y, sp_w, sp_h)
        self._right_plate = QRectF(w - self.SIDE_INSET - sp_w, sp_y, sp_w, sp_h)

        # RIBBON / cartouche under the escutcheon (within the plate's lower band).
        tiny_h = QFontMetrics(Fonts.tiny()).height()
        ribbon_h = tiny_h + 4
        ribbon_w = plate_w + 24.0
        self._ribbon_rect = QRectF((w - ribbon_w) / 2.0,
                                   plate_y + plate_h - ribbon_h - 2.0,
                                   ribbon_w, ribbon_h)

    def _shield_path(self, r: QRectF) -> QPainterPath:
        """A heraldic shield/escutcheon: rounded shoulders + a pointed chief at
        the base (the crest drawPolygon idiom, as a smooth path)."""
        path = QPainterPath()
        x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
        rad = min(16.0, ww * 0.18)
        tip_h = hh * 0.20                    # the bottom point depth
        body_bottom = y + hh - tip_h
        path.moveTo(x + rad, y)
        path.lineTo(x + ww - rad, y)
        path.quadTo(x + ww, y, x + ww, y + rad)
        path.lineTo(x + ww, body_bottom)
        # sweep down to the centered point.
        path.quadTo(x + ww, y + hh - tip_h * 0.4, x + ww / 2.0, y + hh)
        path.quadTo(x, y + hh - tip_h * 0.4, x, body_bottom)
        path.lineTo(x, y + rad)
        path.quadTo(x, y, x + rad, y)
        path.closeSubpath()
        return path

    def resizeEvent(self, event):
        self._build_geometry()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    #  Paint
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_strap(p)
        if self._locked:
            self._paint_locked(p)
        elif self._mow is None or self._mow.is_empty:
            self._paint_empty(p)
        else:
            self._paint_side_plate(p, self._left_plate, "WEEK SPEND",
                                   f"${self._mow.week_spend:,.2f}")
            self._paint_side_plate(p, self._right_plate, "TOKENS",
                                   _fmt_tokens(self._mow.week_tokens))
            self._paint_escutcheon(p)
            self._paint_ribbon(p)
        p.end()

    def _paint_strap(self, p):
        r = self._strap_rect
        if r is None:
            return
        grad = QLinearGradient(0, 0, 0, r.height())
        if self._locked:
            grad.setColorAt(0.0, _BELT_LOCKED_STRAP)
            grad.setColorAt(1.0, QColor(0x1A, 0x1A, 0x20))
        else:
            grad.setColorAt(0.0, _BELT_STRAP_TOP)
            grad.setColorAt(1.0, _BELT_STRAP_BOT)
        body = QPainterPath()
        body.addRoundedRect(r, self.STRAP_RADIUS, self.STRAP_RADIUS)
        p.fillPath(body, QBrush(grad))
        # dashed inner stitching, inset STITCH_INSET.
        inset = r.adjusted(self.STITCH_INSET, self.STITCH_INSET,
                           -self.STITCH_INSET, -self.STITCH_INSET)
        p.setPen(self._stitch_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        stitch = QPainterPath()
        stitch.addRoundedRect(inset, self.STRAP_RADIUS - 2, self.STRAP_RADIUS - 2)
        p.drawPath(stitch)

    def _paint_gold_plate(self, p, path_or_rect, locked_ok=True):
        """Fill a gold plate (shield path OR a rounded rect) with the two-stop
        gold gradient + the rim. Greyed when locked."""
        if isinstance(path_or_rect, QPainterPath):
            bounds = path_or_rect.boundingRect()
        else:
            bounds = path_or_rect
        grad = QLinearGradient(bounds.x(), bounds.y(),
                               bounds.x(), bounds.y() + bounds.height())
        if self._locked and locked_ok:
            grad.setColorAt(0.0, _BELT_LOCKED_PLATE)
            grad.setColorAt(1.0, QColor(0x2A, 0x2A, 0x34))
            rim = QPen(QColor(0x4A, 0x4A, 0x56), 2)
        else:
            grad.setColorAt(0.0, _BELT_PLATE_TOP)
            grad.setColorAt(1.0, _BELT_PLATE_BOT)
            rim = self._rim_pen
        p.setBrush(QBrush(grad))
        p.setPen(rim)
        if isinstance(path_or_rect, QPainterPath):
            p.drawPath(path_or_rect)
        else:
            p.drawRoundedRect(bounds, 6, 6)

    def _paint_escutcheon(self, p):
        mow = self._mow
        path = self._plate_path
        if path is None:
            return
        self._paint_gold_plate(p, path)
        # the champion's spend hue as a THIN keyline just inside the rim.
        accent = self._champion_accent()
        kp = QPen(accent, 1.4)
        p.setPen(kp)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._shield_path(self._plate_rect.adjusted(3, 3, -3, -3)))

        # the glint: a narrow white-alpha band swept across the plate ONCE.
        self._paint_glint(p, path)

        # logo OR monogram disc.
        self._paint_logo(p)

        # engraved name + share, below the logo.
        pr = self._plate_rect
        name_top = self._logo_rect.bottom() + 3
        name_rect = QRectF(pr.x() + 6, name_top, pr.width() - 12,
                           QFontMetrics(Fonts.label()).height())
        name = QFontMetrics(Fonts.label()).elidedText(
            mow.champion_name, Qt.TextElideMode.ElideRight, int(pr.width() - 12))
        self._engrave(p, name_rect, name, Fonts.label(), _BELT_ENGRAVE)
        share_rect = QRectF(pr.x() + 6, name_rect.bottom() + 1, pr.width() - 12,
                            QFontMetrics(Fonts.tiny()).height())
        share_txt = f"{mow.share_pct:.0f}% OF THIS WEEK"
        self._engrave(p, share_rect, share_txt, Fonts.tiny(), _BELT_ENGRAVE_MUTE)

    def _paint_glint(self, p, path):
        g = self._glint
        if g <= 0.0 or g >= 1.0:
            return
        bounds = path.boundingRect()
        p.save()
        p.setClipPath(path)
        band_w = bounds.width() * 0.28
        # sweep the band left->right across the plate as glint goes 0->1.
        cx = bounds.x() - band_w + (bounds.width() + 2 * band_w) * g
        grad = QLinearGradient(cx - band_w / 2.0, 0, cx + band_w / 2.0, 0)
        edge = QColor(255, 255, 255, 0)
        peak = QColor(255, 255, 255, 120)
        grad.setColorAt(0.0, edge)
        grad.setColorAt(0.5, peak)
        grad.setColorAt(1.0, edge)
        p.fillRect(bounds, QBrush(grad))
        p.restore()

    def _paint_logo(self, p):
        lr = self._logo_rect
        if lr is None:
            return
        px = self._champion_pixmap()
        if px is not None:
            # rounded-tile clip so the logo reads as a coin on the gold.
            p.save()
            clip = QPainterPath()
            clip.addEllipse(lr)
            p.setClipPath(clip)
            p.drawPixmap(lr.toRect(), px)
            p.restore()
            p.setPen(QPen(_BELT_PLATE_RIM, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(lr)
            return
        # MONOGRAM disc: the first letter on the champion's accent keyline.
        accent = self._champion_accent()
        disc = QColor(_BELT_ENGRAVE)
        p.setBrush(QBrush(disc))
        p.setPen(QPen(accent, 1.6))
        p.drawEllipse(lr)
        f = Fonts.mono_medium()
        p.setFont(f)
        p.setPen(QPen(_BELT_PLATE_TOP))
        p.drawText(lr, Qt.AlignmentFlag.AlignCenter, self._monogram_letter())

    def _paint_side_plate(self, p, rect, label, value):
        if rect is None:
            return
        self._paint_gold_plate(p, rect)
        # label (muted, top) + value (ink, mono, below).
        lab_rect = QRectF(rect.x(), rect.y() + 3, rect.width(),
                          QFontMetrics(Fonts.tiny()).height())
        self._engrave(p, lab_rect, label, Fonts.tiny(), _BELT_ENGRAVE_MUTE)
        val_rect = QRectF(rect.x(), lab_rect.bottom() + 1, rect.width(),
                          rect.bottom() - lab_rect.bottom() - 3)
        val_font = Fonts.mono_small()
        val = QFontMetrics(val_font).elidedText(
            value, Qt.TextElideMode.ElideRight, int(rect.width() - 6))
        self._engrave(p, val_rect, val, val_font, _BELT_ENGRAVE)

    def _paint_ribbon(self, p):
        rr = self._ribbon_rect
        mow = self._mow
        if rr is None or mow is None:
            return
        if self._has_momentum():
            self._paint_momentum_cartouche(p, rr, mow)
        else:
            # WEEK-1: a muted banner, NO arrow, NO color claim (TEXT_MUTED).
            self._paint_ribbon_band(p, rr, QColor(_BELT_STRAP_TOP).darker(110))
            p.setFont(Fonts.tiny())
            p.setPen(QPen(Colors.TEXT_MUTED))
            p.drawText(rr, Qt.AlignmentFlag.AlignCenter, "WEEK 1 · NO PRIOR ROUND")

    def _paint_ribbon_band(self, p, rr, fill):
        band = QPainterPath()
        band.addRoundedRect(rr, 4, 4)
        p.fillPath(band, QBrush(fill))
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(band)

    def _paint_momentum_cartouche(self, p, rr, mow):
        """The green/red WoW cartouche (echo #7 THE TAPE): a chevron + the signed
        share delta — ONLY drawn when a real 2nd-week delta exists."""
        delta = mow.wow_delta or 0.0
        up = delta >= 0
        col = Colors.GREEN if up else Colors.RED
        band = QColor(col); band.setAlpha(36)
        self._paint_ribbon_band(p, rr, band)
        # the chevron at the left of the band.
        cy = rr.center().y()
        cxx = rr.x() + 12
        ch = 5.0
        tri = QPolygonF()
        if up:
            tri.append(QPointF(cxx, cy + ch))
            tri.append(QPointF(cxx + 8, cy + ch))
            tri.append(QPointF(cxx + 4, cy - ch))
        else:
            tri.append(QPointF(cxx, cy - ch))
            tri.append(QPointF(cxx + 8, cy - ch))
            tri.append(QPointF(cxx + 4, cy + ch))
        p.setBrush(QBrush(col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tri)
        # the signed share delta text.
        sign = "+" if up else ""
        txt = f"{sign}{delta * 100.0:.0f}% share vs last wk"
        p.setFont(Fonts.tiny())
        p.setPen(QPen(col))
        p.drawText(QRectF(cxx + 14, rr.y(), rr.width() - (cxx + 14 - rr.x()) - 6,
                          rr.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)

    def _engrave(self, p, rect, text, font, ink):
        """Engraved text on gold: a 1px light highlight (down-right) under a dark
        ink (so it reads as struck into the plate)."""
        if not text:
            return
        p.setFont(font)
        hi = QColor(255, 255, 255, 70)
        p.setPen(QPen(hi))
        p.drawText(rect.translated(0.6, 0.6),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(QPen(ink))
        p.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   text)

    def _paint_empty(self, p):
        # zero buckets -> a tidy gold-less strap message (no champion engraved).
        r = self._strap_rect
        p.setFont(Fonts.body())
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "No spend yet this week")

    def _paint_locked(self, p):
        """Ghosted grey belt: a greyed escutcheon silhouette + a padlock glyph +
        the canonical unlock copy. Mirrors the _paint_locked idiom (zero fake
        data)."""
        path = self._plate_path
        if path is not None:
            self._paint_gold_plate(p, path)   # locked -> greyed by the flag
        # padlock glyph centered in the plate.
        pr = self._plate_rect
        if pr is not None:
            f = Fonts.mono_medium()
            p.setFont(f)
            p.setPen(QPen(QColor(0x8A, 0x8A, 0x9A)))
            p.drawText(QRectF(pr.x(), pr.y() + self.PAD, pr.width(), self.LOGO_PX),
                       Qt.AlignmentFlag.AlignCenter, "\U0001F512")  # 🔒
        # the unlock copy across the strap, below the plate.
        r = self._strap_rect
        p.setFont(Fonts.tiny())
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(QRectF(r.x(), r.height() - 18, r.width(), 16),
                   Qt.AlignmentFlag.AlignCenter, "Unlock to crown your weekly model")

    # ------------------------------------------------------------------
    #  Interaction — the whole belt is the click target -> the week dossier
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self._locked or self._mow is None or self._mow.is_empty:
            super().mousePressEvent(event)
            return
        gy = int(self.mapToGlobal(QPoint(0, int(self._strap_h / 2))).y())
        self.week_clicked.emit(gy)


# ---- #16 week DOSSIER (the tap-through popup) -----------------------------
class WeekDossierStrip(QWidget):
    """The painted week dossier body for THE TITLE BELT: the champion header +
    the exact week spend/tokens/requests + the runner-up trace. All text is
    QPainter-drawn (injection-safe); the HTML wrapper ALSO html.escapes names.
    devicePixelRatio-aware. Measure-before-allocate so nothing clips."""

    STRIP_W = 300
    PAD = 12
    ROW_H = 18
    ROW_GAP = 4

    def __init__(self, mow, parent=None):
        super().__init__(parent)
        self._m = mow
        self._rows = self._build_rows()
        self._h = self._measure_height()
        self.setFixedSize(self.STRIP_W, self._h)

    def _build_rows(self):
        m = self._m
        rows = [
            ("week", m.date_label),
            ("spend", f"${m.week_spend:,.2f}"),
            ("tokens", f"{m.week_tokens:,}"),
            ("requests", f"{m.week_requests:,}"),
            ("share", f"{m.share_pct:.1f}% of this week"),
        ]
        if m.runner_up_id:
            rows.append(("runner-up",
                         f"{m.runner_up_name} (${m.runner_up_spend:,.2f})"))
        return rows

    def _measure_height(self) -> int:
        head_h = QFontMetrics(Fonts.mono_small()).height() + 6
        body = len(self._rows) * (self.ROW_H + self.ROW_GAP) - self.ROW_GAP
        return int(self.PAD * 2 + head_h + 6 + body)

    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        pm = QPixmap(int(self.STRIP_W * dpr), int(self._h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        w = self.STRIP_W
        pad = self.PAD
        m = self._m
        f_head = Fonts.mono_small()
        fm_head = QFontMetrics(f_head)
        head = _coin_short_name(m.champion_name) or m.champion_id
        p.setFont(f_head)
        p.setPen(QPen(_GOLD_INK))
        p.drawText(QRectF(pad, pad, w - 2 * pad, fm_head.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"\U0001F3C6 {head}")        # 🏆
        y = pad + fm_head.height() + 6
        f_lab = Fonts.tiny()
        f_val = Fonts.mono_small()
        for label, value in self._rows:
            p.setFont(f_lab)
            p.setPen(QPen(Colors.TEXT_SECONDARY))
            p.drawText(QRectF(pad, y, 78, self.ROW_H),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       label)
            p.setFont(f_val)
            p.setPen(QPen(Colors.TEXT_PRIMARY))
            p.drawText(QRectF(pad + 80, y, w - pad - 80 - pad, self.ROW_H),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       value)
            y += self.ROW_H + self.ROW_GAP


def build_week_dossier_html(mow) -> str:
    """The #16 week dossier for the ProviderPopup: a header + the painted detail
    strip embedded as a data-URI <img> (single-QLabel contract) + the full model
    id + the honest 'Week 1' grace line. Every API-sourced string (model/provider
    names) is html.escape'd before it enters the HTML wrapper (the pixmap text is
    QPainter-drawn so it's injection-safe by construction). Returns '' when
    there's no champion."""
    if mow is None or mow.is_empty:
        return ("<div style='font-size:9.5pt;color:#a0a0c8;font-weight:bold;'>"
                "— NO CHAMPION THIS WEEK —</div>")
    full_id = html.escape(mow.champion_id)
    name = html.escape(mow.champion_name or mow.champion_id)
    out = [
        f"<div style='font-size:11pt;font-weight:bold;color:#E8C46A;'>"
        f"\U0001F3C6 {name} — the week's title belt</div>",
        f"<div style='color:#64648c;font-size:8pt;margin-bottom:6px;'>"
        f"{full_id}</div>",
    ]
    try:
        strip = WeekDossierStrip(mow)
        pm = strip.render_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        out.append(
            f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
            f"width='{WeekDossierStrip.STRIP_W}' height='{strip._h}'></div>")
    except Exception:
        log.debug("week dossier render failed", exc_info=True)
    # The honest comparison footnote: Week-1 grace OR the real momentum line.
    if mow.wow_delta is None:
        out.append(
            "<div style='margin-top:2px;color:#9AA0AD;font-size:8pt;'>"
            "Week 1 — a runner-up belt appears once you log a second week.</div>")
    else:
        up = mow.wow_delta >= 0
        col = "#2ED573" if up else "#FF4757"
        sign = "+" if up else ""
        out.append(
            f"<div style='margin-top:2px;color:{col};font-size:8pt;'>"
            f"{sign}{mow.wow_delta * 100:.0f}% share vs last week.</div>")
    return "".join(out)


# ===========================================================================
#  #17 THE FLIGHT RECORDER — Token Odometer + Records + Streak
#  (the THIRD Insights widget, under #16, above the #18 slot)
# ===========================================================================
# A dedicated WARM BRASS/AMBER lane no sibling owns — deliberately warmer/more
# industrial than #16's trophy gold (the rolling-drum FORM + the warm dark panel
# disambiguate even though both are goldish). NEVER model-derived; never touches
# Spend's spend_palette.model_color. (decision E)
_REC_AMBER = QColor(0xE8, 0xA2, 0x3D)       # the value / lit color
_REC_AMBER_HI = QColor(0xF6, 0xC6, 0x6B)    # drum highlight center / glow
_REC_BRASS_DARK = QColor(0x6B, 0x4A, 0x1E)  # drum window top/bottom cylinder shadow
_REC_PANEL = QColor(0x14, 0x11, 0x0C)       # the warm near-black panel fill
_REC_BEZEL = QColor(0x2A, 0x21, 0x14)       # the 1px inner bezel stroke
_REC_INACTIVE = QColor(0x4A, 0x40, 0x30)    # unlit runway rings
_REC_DRUM_INK = QColor(0x1A, 0x14, 0x09)    # the digit on the bright drum face
_REC_STRIP_PANEL = QColor(0x0D, 0x0B, 0x07)  # the black-box strip inset (darker)


def _fmt_tokens_compact(n: int) -> str:
    """A compact token magnitude for the flight strip ('6634418' -> '6.63M').
    No ' tok' suffix (the strip appends its own)."""
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n}"


def _fmt_record_date(iso: str) -> str:
    """'2026-06-22' -> 'Jun 22' for the flight strip stamp. Falls back to the raw
    string if it isn't an ISO date (defensive — never raises)."""
    if not iso:
        return ""
    try:
        d = datetime.date.fromisoformat(iso[:10])
        return d.strftime("%b %d").replace(" 0", " ")   # 'Jun 02' -> 'Jun 2'
    except (ValueError, TypeError):
        return iso


class TokenRecorder(QWidget):
    """#17 THE FLIGHT RECORDER — a brass cockpit instrument that fuses three thin
    facts into one short-but-real flight log (the third Insights widget, under
    #16's belt).

    ONE rounded brushed-metal panel (REC_PANEL fill + a 1px REC_BEZEL inner
    stroke) painted top->bottom by a SINGLE _paint_into(p) shared by paintEvent
    AND render_pixmap. Three stacked bands:
      BAND A — ODOMETER DRUM: a row of digit drums (a vertical brass gradient with
        a bright center faking cylinder curve, the digit centered, a faint sliver
        of the NEXT digit peeking at the window bottom + a 1px seam line), comma
        separators as half-width drums, the 'TOKENS ROUTED · LIFETIME' hairline
        label + a 'tok' tag — reading the LIFETIME token total.
      BAND B — BLACK-BOX FLIGHT STRIP: a darker inset plaque with a left amber
        spine + a static 'REC' dot, the record day's big amber '$4.37', a 'Jun 22'
        stamp, and a micro '6.63M tok · 97 req'. A 2nd dimmer strip auto-appears
        ONLY if the biggest-TOKEN day differs from the biggest-SPEND day (decision
        E; today they coincide -> one strip).
      BAND C — RUNWAY: a thin centerline with 7 landing-light slots — active days
        lit amber + a soft glow, inactive dark rings, the current run connected by
        a brighter bar, a right-aligned 'N-DAY RUN' caption.

    set_data(TokenRecord) paints the instrument; None keeps last-good. set_locked()
    paints a dimmed drum reading the LOCKED_SENTINEL ('— — —') + a key glyph + the
    canonical unlock copy (NEVER a zeroed '0' that could read as real, decision D).
    EMPTY (key present, zero active days) -> a tidy 'No traffic logged yet'
    instrument (real zeros honest). Clicking the card emits
    recorder_clicked(anchor_y_global) -> the dashboard's flight-recorder dossier.

    Motion (decision C): THE Insights zone's count-up owner. ONE held
    QPropertyAnimation drives a DISTINCT `_roll` Property (NOT a QWidget builtin —
    the widget never moves) 0->1 over ~900ms OutCubic on first set_data / a value
    INCREASE; the displayed lifetime = int(target * _roll) re-formatted f'{v:,}'
    every frame and RIGHT-aligned so the drums roll UP like a gas pump. A same-
    value 15-min re-poll does NOT re-animate (gated on a stored _last_lifetime).
    The runway lights stagger off the SAME _roll driver (no 2nd animation object);
    the 'REC' dot is STATIC. Reduce-motion -> _roll parked at 1.0 instantly.
    Allocation-free paint: pens/brushes + the measured geometry are built in
    set_data/_measure, never paintEvent."""

    recorder_clicked = Signal(int)         # global anchor y for the dossier popup

    LOCKED_SENTINEL = "— — —"   # '— — —' (NOT '0', decision D)
    RUNWAY_SLOTS = 7                       # the last-7 landing-light pads

    # -- geometry constants (the measure pass derives everything from these) --
    PANEL_RADIUS = 12
    PAD = 14
    DRUM_GAP = 2                           # ridge between drums
    DRUM_PAD_BOLD = 8                      # window inset around a bold digit
    DRUM_PAD_REG = 6                       # tighter inset under the regular weight

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rec = None                   # token_recorder.TokenRecord | None
        self._locked = False
        self._roll = 1.0                   # 0..1 odometer roll position (animated once)
        self._last_lifetime = None         # value-CHANGED gate (None == fresh)

        # Cached from the measure pass (rebuilt in set_data/resize) — paint reads
        # only these, never recomputes geometry:
        self._panel_h = 0
        self._cells = []                   # list[str] the drum digit/comma cells
        self._drum_compact = False         # regular-weight fallback (overflow)
        self._drum_font = None             # the chosen drum QFont (bold|regular)
        self._cell_w = 0.0                 # a full digit cell width
        self._comma_w = 0.0                # a half-width comma cell
        self._window_h = 0.0               # the drum window height
        self._bandA_top = 0.0
        self._bandB_top = 0.0
        self._bandC_top = 0.0
        self._inner = None                 # the padded inner QRectF

        # Pre-built strokes (allocation-free paint).
        self._bezel_pen = QPen(_REC_BEZEL, 1)
        self._seam_pen = QPen(QColor(0, 0, 0, 110), 1)
        self._ridge_pen = QPen(QColor(0, 0, 0, 90), 1)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # ONE held animation (ArcGauge/Assay idiom) — never per-frame alloc.
        self._anim = QPropertyAnimation(self, b"roll")
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._measure()
        theme_controller.changed.connect(self.update)

    # -- the _roll Property (DISTINCT name; NOT a QWidget builtin) --
    def get_roll(self):
        return self._roll

    def set_roll(self, v):
        self._roll = float(v)
        self.update()

    roll = Property(float, get_roll, set_roll)

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def set_data(self, rec):
        """rec: a token_recorder.TokenRecord (or None). None => keep last-good (the
        instrument never blanks). The roll fires ONCE when the lifetime CHANGES; an
        identical re-distribution (same lifetime) repaints silently (decision C)."""
        if rec is None:
            return
        self._locked = False
        self._rec = rec
        target = int(rec.lifetime_tokens or 0)
        changed = (self._last_lifetime is None or target != self._last_lifetime)
        self._last_lifetime = target
        self._measure()
        self._build_geometry()
        if changed:
            self._start_roll()
        else:
            self._roll = 1.0
        self.update()

    def set_locked(self):
        """No management key: a dimmed drum reading the LOCKED_SENTINEL ('— — —')
        + a key glyph + the canonical unlock copy. The locked height MATCHES the
        populated height so the section never jumps. ZERO fake data — NEVER a
        zeroed '0' that could read as real (decision D)."""
        self._locked = True
        self._rec = None
        self._last_lifetime = None
        self._roll = 1.0
        self._measure()
        self._build_geometry()
        self.update()

    # ------------------------------------------------------------------
    #  State / readout helpers (read by paint + the tests)
    # ------------------------------------------------------------------
    def _drum_string(self) -> str:
        """The string the odometer drums currently show. LOCKED -> the sentinel.
        Populated -> the displayed lifetime = int(target * _roll), comma-grouped
        and RIGHT-aligned by the cell layout (drums roll UP like a gas pump)."""
        if self._locked:
            return self.LOCKED_SENTINEL
        if self._rec is None or self._rec.is_empty:
            return "0"
        target = int(self._rec.lifetime_tokens or 0)
        shown = int(target * max(0.0, min(1.0, self._roll)))
        return f"{shown:,}"

    def _target_string(self) -> str:
        """The FINAL (settled) drum string — drives the measure pass / cell count
        so the geometry is stable across the whole roll (the row never reflows)."""
        if self._locked:
            return self.LOCKED_SENTINEL
        if self._rec is None or self._rec.is_empty:
            return "0"
        return f"{int(self._rec.lifetime_tokens or 0):,}"

    def _runway_slots(self) -> int:
        return self.RUNWAY_SLOTS

    def _lit_count(self) -> int:
        """How many runway pads are lit = the last-active-run, clamped to the slot
        count. 0 when locked / empty."""
        if self._locked or self._rec is None or self._rec.is_empty:
            return 0
        return max(0, min(self.RUNWAY_SLOTS, int(self._rec.streak_run or 0)))

    def _run_caption(self) -> str:
        """The honest right-aligned runway caption. Always the LAST-ACTIVE-RUN
        length — NEVER an ongoing-today claim today's emptiness contradicts
        (decision B). '3-DAY RUN' / '1-DAY RUN'; empty -> 'NO RUNS YET'."""
        if self._locked:
            return ""
        if self._rec is None or self._rec.is_empty or self._rec.streak_run <= 0:
            return "NO RUNS YET"
        n = int(self._rec.streak_run)
        return f"{n}-DAY RUN"

    # ------------------------------------------------------------------
    #  Roll animation (one-time, value-CHANGED gated) — decision C
    # ------------------------------------------------------------------
    def _start_roll(self):
        try:
            import anim
            on = anim.ANIMATIONS_ON
        except Exception:
            on = True
        self._anim.stop()
        if not on or not self.isVisible():
            self._roll = 1.0           # reduce-motion -> snap to the full value
            return
        self._roll = 0.0
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    # ------------------------------------------------------------------
    #  Measure pass (drives BOTH paint and setFixedHeight — no clipping).
    #  Font-metric-driven; the SAME pass feeds _paint_into + sizeHint. The drum
    #  row drops to the regular weight + a tighter pad if the bold row would
    #  overflow the inner width (decision: the regular-weight fallback recompute).
    # ------------------------------------------------------------------
    def _measure(self):
        inner_w = max(40.0, self.width() - 2 * self.PAD)

        # The cells are the FINAL string's characters (digits + commas), so the
        # geometry is stable across the whole roll (the row never reflows).
        s = self._target_string()
        self._cells = list(s)

        def _row_metrics(font, pad):
            fm = QFontMetrics(font)
            digit_w = fm.horizontalAdvance("0")
            cell_w = digit_w + pad
            comma_w = max(6.0, digit_w * 0.55)
            window_h = fm.height() + 10.0
            total = 0.0
            for ch in self._cells:
                total += (comma_w if ch == "," else cell_w)
            total += max(0, len(self._cells) - 1) * self.DRUM_GAP
            return cell_w, comma_w, window_h, total

        bold = Fonts.mono_medium()         # the bold drum digit
        cell_w, comma_w, window_h, total = _row_metrics(bold, self.DRUM_PAD_BOLD)
        if total > inner_w:
            # OVERFLOW -> drop to the regular weight + a tighter pad, recompute.
            reg = Fonts.mono_small()
            cell_w, comma_w, window_h, total = _row_metrics(reg, self.DRUM_PAD_REG)
            self._drum_font = reg
            self._drum_compact = True
        else:
            self._drum_font = bold
            self._drum_compact = False
        self._cell_w = cell_w
        self._comma_w = comma_w
        self._window_h = window_h

        label_h = QFontMetrics(Fonts.label()).height()
        # BAND A = label + window + the 'tok' tag baseline (small gap).
        bandA_h = label_h + 4 + window_h
        # BAND B = the flight strip: 2 mono rows + paddings. A 2nd strip stacks
        # when the biggest-token day diverges from the biggest-spend day.
        strip_one = QFontMetrics(Fonts.mono_medium()).height() + \
            QFontMetrics(Fonts.tiny()).height() + 16
        n_strips = 2 if (self._rec is not None and not self._locked and
                         self._rec.has_second_strip) else 1
        bandB_h = strip_one * n_strips + (6 if n_strips == 2 else 0)
        # BAND C = the runway: a pad row + a caption line.
        cap_h = QFontMetrics(Fonts.tiny()).height()
        bandC_h = 22 + cap_h

        gap = 12
        self._panel_h = int(self.PAD + bandA_h + gap + bandB_h + gap +
                            bandC_h + self.PAD)
        self.setFixedHeight(self._panel_h)

    def sizeHint(self) -> QSize:
        return QSize(320, self._panel_h)

    # ------------------------------------------------------------------
    #  Geometry build (cache the inner rect + the band tops). Runs in
    #  set_data/resize — NOT the paint hot path.
    # ------------------------------------------------------------------
    def _build_geometry(self):
        w = max(1, self.width())
        h = self._panel_h
        self._inner = QRectF(self.PAD, self.PAD, w - 2 * self.PAD,
                             h - 2 * self.PAD)
        label_h = QFontMetrics(Fonts.label()).height()
        gap = 12
        self._bandA_top = self.PAD
        bandA_h = label_h + 4 + self._window_h
        self._bandB_top = self._bandA_top + bandA_h + gap
        strip_one = QFontMetrics(Fonts.mono_medium()).height() + \
            QFontMetrics(Fonts.tiny()).height() + 16
        n_strips = 2 if (self._rec is not None and not self._locked and
                         self._rec.has_second_strip) else 1
        bandB_h = strip_one * n_strips + (6 if n_strips == 2 else 0)
        self._bandC_top = self._bandB_top + bandB_h + gap

    def resizeEvent(self, event):
        self._measure()
        self._build_geometry()
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    #  Paint — ONE _paint_into shared by paintEvent AND render_pixmap.
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        if self._inner is None:
            self._build_geometry()
        w = max(1, self.width())
        h = self._panel_h
        # the warm brushed-metal panel + the 1px bezel.
        panel = QPainterPath()
        panel.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1),
                             self.PANEL_RADIUS, self.PANEL_RADIUS)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, _REC_PANEL.lighter(118))
        grad.setColorAt(0.5, _REC_PANEL)
        grad.setColorAt(1.0, _REC_PANEL.darker(115))
        p.fillPath(panel, QBrush(grad))
        p.setPen(self._bezel_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(panel)

        self._paint_band_a(p)              # the odometer drum (always)
        if self._locked:
            self._paint_locked_lower(p)
        elif self._rec is None or self._rec.is_empty:
            self._paint_empty_lower(p)
        else:
            self._paint_band_b(p)          # the black-box flight strip
            self._paint_band_c(p)          # the runway streak

    # ---- BAND A: the odometer drum -----------------------------------
    def _paint_band_a(self, p):
        inner = self._inner
        label_h = QFontMetrics(Fonts.label()).height()
        # hairline label, letter-spaced amber.
        p.setFont(Fonts.label())
        p.setPen(QPen(_REC_AMBER.darker(105)))
        p.drawText(QRectF(inner.x(), self._bandA_top, inner.width(), label_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "TOKENS ROUTED · LIFETIME")

        # the drum row, RIGHT-aligned within the inner width (a 'tok' tag sits to
        # the right of the last drum, so reserve its advance first).
        win_top = self._bandA_top + label_h + 4
        cells = self._cells
        # measure the row width from the cached cell metrics.
        row_w = 0.0
        for ch in cells:
            row_w += (self._comma_w if ch == "," else self._cell_w)
        row_w += max(0, len(cells) - 1) * self.DRUM_GAP

        tok_font = Fonts.tiny()
        tok_w = QFontMetrics(tok_font).horizontalAdvance(" tok") + 4
        x0 = inner.x() + inner.width() - tok_w - row_w
        x0 = max(inner.x(), x0)            # never spill left of the panel

        shown = self._drum_string()
        # right-align the shown string into the FINAL cell slots: pad with blanks
        # on the LEFT so lower drums change while the leading drums stay parked.
        pad_n = max(0, len(cells) - len(shown))
        glyphs = [""] * pad_n + list(shown)
        if len(glyphs) > len(cells):       # defensive (shouldn't exceed)
            glyphs = glyphs[-len(cells):]

        x = x0
        for i, ch in enumerate(cells):
            is_comma = (ch == ",")
            cw = self._comma_w if is_comma else self._cell_w
            glyph = glyphs[i] if i < len(glyphs) else ""
            self._paint_drum_cell(p, QRectF(x, win_top, cw, self._window_h),
                                  glyph, is_comma)
            if i < len(cells) - 1:
                # a thin ridge between drums.
                rx = x + cw + self.DRUM_GAP / 2.0
                p.setPen(self._ridge_pen)
                p.drawLine(QPointF(rx, win_top + 2),
                           QPointF(rx, win_top + self._window_h - 2))
            x += cw + self.DRUM_GAP

        # the 'tok' tag baseline-aligned to the right of the last drum.
        p.setFont(tok_font)
        p.setPen(QPen(_REC_AMBER.darker(115)))
        p.drawText(QRectF(x + 2, win_top, tok_w, self._window_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   "tok")

    def _paint_drum_cell(self, p, r: QRectF, glyph: str, is_comma: bool):
        """One digit/comma drum: a rounded window with a vertical brass gradient
        (bright center, dark top+bottom faking the cylinder curve), the glyph
        centered, a faint peeking next-digit sliver at the bottom + a 1px seam
        line (a physical wheel mid-rotation). Dimmed when locked."""
        cell = QPainterPath()
        cell.addRoundedRect(r, 3, 3)
        grad = QLinearGradient(r.x(), r.y(), r.x(), r.y() + r.height())
        if self._locked:
            grad.setColorAt(0.0, QColor(0x30, 0x2A, 0x20))
            grad.setColorAt(0.5, QColor(0x3A, 0x33, 0x26))
            grad.setColorAt(1.0, QColor(0x24, 0x1F, 0x17))
        else:
            grad.setColorAt(0.0, _REC_BRASS_DARK)
            grad.setColorAt(0.18, _REC_AMBER.darker(112))
            grad.setColorAt(0.5, _REC_AMBER_HI)        # bright cylinder center
            grad.setColorAt(0.82, _REC_AMBER.darker(112))
            grad.setColorAt(1.0, _REC_BRASS_DARK)
        p.fillPath(cell, QBrush(grad))
        # the seam line across the cylinder midline (mid-rotation physicality).
        p.setPen(self._seam_pen)
        p.drawLine(QPointF(r.x() + 1, r.center().y()),
                   QPointF(r.right() - 1, r.center().y()))

        if not glyph:
            return
        ink = QColor(0x6A, 0x6A, 0x7A) if self._locked else _REC_DRUM_INK
        # a faint sliver of the NEXT digit peeking at the window bottom (only for
        # real digits, not commas / the locked sentinel) — the rolling tell.
        if not is_comma and not self._locked and glyph.isdigit():
            nxt = str((int(glyph) + 1) % 10)
            p.save()
            clip = QPainterPath()
            sliver = QRectF(r.x(), r.y() + r.height() * 0.74,
                            r.width(), r.height() * 0.26)
            clip.addRect(sliver)
            p.setClipPath(clip)
            p.setFont(self._drum_font)
            peek = QColor(_REC_DRUM_INK); peek.setAlpha(70)
            p.setPen(QPen(peek))
            # draw the next glyph shifted up so only its TOP peeks into the sliver.
            p.drawText(r.translated(0, -r.height() * 0.62),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       nxt)
            p.restore()
        # the current glyph, centered.
        p.setFont(self._drum_font)
        p.setPen(QPen(ink))
        p.drawText(r, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   glyph)

    # ---- BAND B: the black-box flight strip --------------------------
    def _paint_band_b(self, p):
        rec = self._rec
        inner = self._inner
        strip_h = QFontMetrics(Fonts.mono_medium()).height() + \
            QFontMetrics(Fonts.tiny()).height() + 16
        # primary strip = the record-by-SPEND day.
        self._paint_flight_strip(
            p, QRectF(inner.x(), self._bandB_top, inner.width(), strip_h),
            rec.record, dim=False)
        # a 2nd dimmer strip ONLY when the biggest-token day diverges (decision E).
        if rec.has_second_strip:
            self._paint_flight_strip(
                p, QRectF(inner.x(), self._bandB_top + strip_h + 6,
                          inner.width(), strip_h),
                rec.record_by_tokens, dim=True, tag="TOKEN PEAK")

    def _paint_flight_strip(self, p, r: QRectF, day, dim: bool, tag: str = "REC"):
        if day is None or day.is_empty:
            return
        amber = _REC_AMBER.darker(125) if dim else _REC_AMBER
        # the darker inset plaque.
        plaque = QPainterPath()
        plaque.addRoundedRect(r, 6, 6)
        p.fillPath(plaque, QBrush(_REC_STRIP_PANEL))
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(plaque)
        # the left amber spine.
        spine = QRectF(r.x(), r.y() + 3, 3.0, r.height() - 6)
        p.fillRect(spine, QBrush(amber))
        # the static 'REC' dot (a filled amber circle + ring) at the spine top.
        dot_r = 4.0
        dot = QRectF(r.x() + 9, r.y() + 8, dot_r * 2, dot_r * 2)
        p.setBrush(QBrush(amber))
        p.setPen(QPen(amber.darker(140), 1))
        p.drawEllipse(dot)
        p.setFont(Fonts.tiny())
        p.setPen(QPen(amber.darker(115)))
        p.drawText(QRectF(dot.right() + 5, r.y() + 5, 70, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tag)

        # the big amber spend + the date stamp on the value row.
        val_y = r.y() + 6
        f_val = Fonts.mono_medium()
        p.setFont(f_val)
        p.setPen(QPen(amber if not dim else amber.lighter(110)))
        val_txt = f"${day.spend:,.2f}"
        p.drawText(QRectF(r.x() + 14, val_y + 14, r.width() * 0.5,
                          QFontMetrics(f_val).height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   val_txt)
        # the 'Jun 22' stamp, right-aligned on the value row.
        p.setFont(Fonts.mono_small())
        p.setPen(QPen(QColor(0xC8, 0xB8, 0x98)))
        p.drawText(QRectF(r.x() + r.width() * 0.45, val_y + 14,
                          r.width() * 0.55 - 10, QFontMetrics(f_val).height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   _fmt_record_date(day.date))
        # the micro 'tok · req' line.
        p.setFont(Fonts.tiny())
        p.setPen(QPen(QColor(0x8A, 0x80, 0x6A)))
        micro = f"{_fmt_tokens_compact(day.tokens)} tok · {day.reqs} req"
        p.drawText(QRectF(r.x() + 14, r.bottom() - 16, r.width() - 20, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   micro)

    # ---- BAND C: the runway streak -----------------------------------
    def _paint_band_c(self, p):
        inner = self._inner
        lit = self._lit_count()
        slots = self.RUNWAY_SLOTS
        cap_h = QFontMetrics(Fonts.tiny()).height()
        pad_y = self._bandC_top + 8
        # the thin centerline.
        cl_y = pad_y
        p.setPen(QPen(QColor(0x3A, 0x33, 0x24), 1))
        p.drawLine(QPointF(inner.x() + 4, cl_y),
                   QPointF(inner.x() + inner.width() - 4, cl_y))

        spacing = inner.width() / float(slots)
        pad_r = 4.0
        # the run is the RIGHTMOST `lit` pads (the most-recent days), so the
        # current run sits at the approach end of the runway.
        first_lit = slots - lit
        # the brighter connector bar under the current run.
        if lit >= 1:
            bx0 = inner.x() + spacing * (first_lit + 0.5)
            bx1 = inner.x() + spacing * (slots - 0.5)
            bar = QColor(_REC_AMBER); bar.setAlpha(150)
            p.setPen(QPen(bar, 2))
            p.drawLine(QPointF(bx0, cl_y), QPointF(bx1, cl_y))

        for i in range(slots):
            cx = inner.x() + spacing * (i + 0.5)
            is_lit = (i >= first_lit) and (lit > 0)
            # stagger the lights off the SAME _roll driver (decision C).
            if is_lit:
                idx_in_run = i - first_lit
                lit_now = self._roll >= ((idx_in_run + 1) / max(1, lit)) - 1e-6
            else:
                lit_now = False
            pad = QRectF(cx - pad_r, cl_y - pad_r, pad_r * 2, pad_r * 2)
            if is_lit and lit_now:
                # a soft glow + a lit amber pad.
                glow = QColor(_REC_AMBER); glow.setAlpha(40)
                p.setBrush(QBrush(glow))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(pad.adjusted(-3, -3, 3, 3))
                p.setBrush(QBrush(_REC_AMBER_HI))
                p.setPen(QPen(_REC_AMBER.darker(130), 1))
                p.drawEllipse(pad)
            else:
                # a dark unlit ring.
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_REC_INACTIVE, 1.4))
                p.drawEllipse(pad)

        # the right-aligned caption.
        p.setFont(Fonts.tiny())
        p.setPen(QPen(_REC_AMBER.darker(110)))
        p.drawText(QRectF(inner.x(), pad_y + 8, inner.width(), cap_h),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._run_caption())

    # ---- LOCKED / EMPTY lower bands ----------------------------------
    def _paint_locked_lower(self, p):
        """The lower two bands when locked: a key glyph + the canonical unlock
        copy (the drum already painted its dimmed sentinel)."""
        inner = self._inner
        y = self._bandB_top
        f = Fonts.mono_medium()
        p.setFont(f)
        p.setPen(QPen(QColor(0x8A, 0x80, 0x6A)))
        p.drawText(QRectF(inner.x(), y, inner.width(),
                          QFontMetrics(f).height() + 6),
                   Qt.AlignmentFlag.AlignCenter, "\U0001F511")   # 🔑
        p.setFont(Fonts.tiny())
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(QRectF(inner.x(), self._bandC_top, inner.width(),
                          QFontMetrics(Fonts.tiny()).height() + 6),
                   Qt.AlignmentFlag.AlignCenter,
                   "Connect a management key to log your traffic")

    def _paint_empty_lower(self, p):
        """Key present, zero active days -> a tidy honest 'No traffic logged yet'
        (real zeros, not a fake record / runway)."""
        inner = self._inner
        p.setFont(Fonts.body())
        p.setPen(QPen(Colors.TEXT_MUTED))
        p.drawText(QRectF(inner.x(), self._bandB_top, inner.width(),
                          self._bandC_top - self._bandB_top),
                   Qt.AlignmentFlag.AlignCenter, "No traffic logged yet")

    # ------------------------------------------------------------------
    #  render_pixmap — the SAME _paint_into, for a dossier thumbnail if ever
    #  needed (parity with the belt; devicePixelRatio-aware).
    # ------------------------------------------------------------------
    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        w = max(1, self.width())
        h = max(1, self._panel_h)
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    # ------------------------------------------------------------------
    #  Interaction — the whole card is the click target -> the dossier
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self._locked or self._rec is None or self._rec.is_empty:
            super().mousePressEvent(event)
            return
        gy = int(self.mapToGlobal(QPoint(0, int(self._panel_h / 2))).y())
        self.recorder_clicked.emit(gy)


# ---- #17 flight-recorder DOSSIER (the tap-through popup) -------------------
class RecorderDossierStrip(QWidget):
    """The painted flight-recorder dossier body for THE FLIGHT RECORDER: the
    lifetime totals header + a TIMELINE of every active day as a mini amber bar
    (reusing the BurnRateBar mini-bar vocabulary) + the record day + the streak
    definition. All text is QPainter-drawn (injection-safe); the HTML wrapper ALSO
    html.escapes the date strings. devicePixelRatio-aware. Measure-before-allocate
    so nothing clips."""

    STRIP_W = 300
    PAD = 12
    ROW_H = 22
    ROW_GAP = 5
    BAR_H = 8

    def __init__(self, rec, parent=None):
        super().__init__(parent)
        self._r = rec
        self._rows = list(rec.series) if rec is not None else []
        # the max spend across the series drives the mini-bar scale.
        self._max_spend = max((d.spend for d in self._rows), default=0.0)
        self._h = self._measure_height()
        self.setFixedSize(self.STRIP_W, self._h)

    def _measure_height(self) -> int:
        head_h = QFontMetrics(Fonts.mono_small()).height() + 6
        body = len(self._rows) * (self.ROW_H + self.ROW_GAP)
        return int(self.PAD * 2 + head_h + 8 + body)

    def render_pixmap(self) -> QPixmap:
        try:
            dpr = self.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        dpr = dpr if dpr and dpr > 0 else 1.0
        pm = QPixmap(int(self.STRIP_W * dpr), int(self._h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._paint_into(p)
        p.end()
        return pm

    def paintEvent(self, event):
        if not _safe_paint(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_into(p)
        p.end()

    def _paint_into(self, p):
        w = self.STRIP_W
        pad = self.PAD
        r = self._r
        f_head = Fonts.mono_small()
        fm_head = QFontMetrics(f_head)
        p.setFont(f_head)
        p.setPen(QPen(_REC_AMBER))
        head = f"✈ {r.lifetime_tokens:,} tok routed · ${r.lifetime_spend:,.2f}"
        p.drawText(QRectF(pad, pad, w - 2 * pad, fm_head.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   head)
        y = pad + fm_head.height() + 8
        # one mini-bar row per active day (the daily timeline).
        for day in self._rows:
            is_rec = (day.date == r.record.date)
            # the date stamp.
            p.setFont(Fonts.tiny())
            p.setPen(QPen(Colors.TEXT_SECONDARY))
            p.drawText(QRectF(pad, y, 52, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       _fmt_record_date(day.date))
            # the spend value (amber-bright for the record day).
            p.setPen(QPen(_REC_AMBER if is_rec else Colors.TEXT_PRIMARY))
            p.setFont(Fonts.mono_small())
            p.drawText(QRectF(w - pad - 92, y, 92, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"${day.spend:,.2f}")
            # the mini bar (BurnRateBar vocabulary): an amber fill scaled to the
            # max-spend day, so the Jun-22 spike dominates honestly.
            bar_y = y + 14
            bw = w - 2 * pad
            track = QRectF(pad, bar_y, bw, self.BAR_H)
            tpath = QPainterPath(); tpath.addRoundedRect(track, 3, 3)
            p.fillPath(tpath, QBrush(QColor(0x2A, 0x24, 0x18)))
            frac = (day.spend / self._max_spend) if self._max_spend > 0 else 0.0
            frac = max(0.02, min(1.0, frac))     # a visible nub even for tiny days
            fill = QRectF(pad, bar_y, bw * frac, self.BAR_H)
            fpath = QPainterPath(); fpath.addRoundedRect(fill, 3, 3)
            col = _REC_AMBER_HI if is_rec else _REC_AMBER.darker(125)
            p.fillPath(fpath, QBrush(col))
            y += self.ROW_H + self.ROW_GAP


def build_recorder_dossier_html(rec) -> str:
    """The #17 flight-recorder dossier for the ProviderPopup: a header + the
    painted daily-timeline strip embedded as a data-URI <img> (single-QLabel
    contract) + the record day + the streak definition SPELLED OUT (the honest
    last-active-run wording, NEVER an ongoing-today claim). Every API-sourced
    string (the ISO dates) is html.escape'd before it enters the HTML wrapper (the
    pixmap text is QPainter-drawn so it's injection-safe by construction). Returns
    '' / a tidy note when there's no traffic."""
    if rec is None or rec.is_empty:
        return ("<div style='font-size:9.5pt;color:#a0a0c8;font-weight:bold;'>"
                "— NO TRAFFIC LOGGED YET —</div>")
    rec_date = html.escape(rec.record.date or "")
    last_date = html.escape(rec.last_active_date or "")
    out = [
        f"<div style='font-size:11pt;font-weight:bold;color:#E8A23D;'>"
        f"✈ The Flight Recorder — your lifetime log</div>",
        f"<div style='color:#8A806A;font-size:8pt;margin-bottom:6px;'>"
        f"{rec.lifetime_tokens:,} tokens · ${rec.lifetime_spend:,.2f} "
        f"· {rec.lifetime_requests:,} requests "
        f"· {rec.active_days} active day(s)</div>",
    ]
    try:
        strip = RecorderDossierStrip(rec)
        pm = strip.render_pixmap()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        out.append(
            f"<div style='margin-bottom:4px;'><img src='data:image/png;base64,{b64}' "
            f"width='{RecorderDossierStrip.STRIP_W}' height='{strip._h}'></div>")
    except Exception:
        log.debug("recorder dossier render failed", exc_info=True)
    # the record day, spelled out.
    out.append(
        f"<div style='margin-top:2px;color:#F6C66B;font-size:8.5pt;'>"
        f"Record day: {rec_date} — ${rec.record.spend:,.2f}, "
        f"{rec.record.tokens:,} tok, {rec.record.reqs} req.</div>")
    # the streak DEFINITION spelled out — honest last-active-run wording.
    if rec.streak_run > 0:
        if rec.streak_is_ongoing_today:
            streak_line = (f"Current run: {rec.streak_run} consecutive active day(s) "
                           f"through today ({last_date}).")
        else:
            streak_line = (f"Last-active run: {rec.streak_run} consecutive active "
                           f"day(s), ending {last_date} (no traffic since).")
    else:
        streak_line = "No active-day run yet."
    out.append(
        f"<div style='margin-top:1px;color:#9AA0AD;font-size:8pt;'>"
        f"{html.escape(streak_line)}</div>")
    out.append(
        "<div style='margin-top:3px;color:#6B6150;font-size:7.5pt;'>"
        "A run is consecutive calendar days with logged traffic; a gap day "
        "resets it.</div>")
    return "".join(out)