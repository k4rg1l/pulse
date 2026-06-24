"""
OpenRouter Pulse - Custom Widgets
Hand-drawn gauges, sparklines, stat cards, status badges.
"""
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

    Click entry points (signals wired now, consumed by #10/#11 later):
      band_clicked(model_id, global_anchor)  -> #10 receipt popup
      spike_clicked(t0_iso, t1_iso)           -> #11 autopsy
    """

    band_clicked = Signal(str, QPointF)
    spike_clicked = Signal(str, str)

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
        painter.end()

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
    #  Interaction (click entry points for #10 / #11)
    # ------------------------------------------------------------------
    def mouseMoveEvent(self, event):
        if self._locked or self._data is None:
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
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
        if self._locked or self._data is None or self._data.is_empty:
            super().mousePressEvent(event)
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        gpos = event.globalPosition() if hasattr(event, "globalPosition") \
            else QPointF(self.mapToGlobal(event.pos()))
        # spike column first (the autopsy entry, #11)
        if self._spike_rect is not None and self._spike_rect.contains(pos):
            si = self._data.spike_index
            if 0 <= si < len(self._data.buckets):
                t0 = self._data.buckets[si]
                t1 = (self._data.buckets[si + 1]
                      if si + 1 < len(self._data.buckets) else t0)
                self.spike_clicked.emit(t0, t1)
                return
        # legend row -> band_clicked (#10 receipt)
        for mid, r in self._legend_rects:
            if r.contains(pos):
                self.band_clicked.emit(mid, gpos)
                return
        # a band polygon in the chart
        for mid, poly in self._band_polys:
            if poly.containsPoint(pos, Qt.FillRule.OddEvenFill):
                self.band_clicked.emit(mid, gpos)
                return
        super().mousePressEvent(event)


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