"""The left vertical icon nav-rail (UI overhaul).

A fully hand-painted rail: a Pulse mark up top, one logo slot per source
(brand logo when a bundled SVG exists, else a monogram), a live severity
status dot on each slot, a spring-animated active indicator + breathing pulse
on the selected source, and Refresh / Settings actions at the bottom.

Source-agnostic: the controller feeds it a list of tab specs; it emits which
source the user picked. Painted (not QPushButtons) for full control of the
look + the active-state motion.
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import (
    Qt, Signal, QRect, QRectF, QPointF, QTimer, QPropertyAnimation,
    QEasingCurve, Property,
)
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QBrush, QFont, QFontMetrics,
    QCursor, QPixmap,
)
from PySide6.QtWidgets import QWidget, QSizePolicy

from config import NAV_RAIL_WIDTH, logo_path
from theme import Colors, Fonts

try:
    from PySide6.QtSvg import QSvgRenderer
    _HAVE_SVG = True
except Exception:  # pragma: no cover - QtSvg should be present with PySide6
    _HAVE_SVG = False

SLOT = 44           # logo slot size
GAP = 8             # gap between slots
TOP = 58            # y where the first source slot starts (below the Pulse mark)


def _mono(source_id: str, name: str) -> str:
    table = {"openrouter": "OR", "claude": "C", "gpu": "GP", "system": "SY"}
    return table.get(source_id, (name[:2] if name else "?").upper())


class NavRail(QWidget):
    source_selected = Signal(str)
    refresh_clicked = Signal()
    settings_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(NAV_RAIL_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self._tabs = []                # [{id,name,accent,logo}]
        self._active = None            # source_id
        self._status = {}              # source_id -> "normal"|"warning"|"critical"
        self._hover = None             # source_id | "refresh" | "settings" | None
        self._logo_cache = {}          # source_id -> QPixmap | None (None=use monogram)
        self._settings_accent = "#00D2FF"
        self._settings_logo = self._load_logo(logo_path("settings"))

        # active-indicator slide (animated in set_active)
        self._indicator_y = float(TOP + SLOT / 2)
        self._ind_anim = QPropertyAnimation(self, b"indicator_y")
        self._ind_anim.setDuration(300)
        self._ind_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        # breathing pulse on the active slot
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._pulse_timer.start(50)

    # ---- public API ----

    def set_sources(self, tabs):
        """tabs: ordered list of dicts {id, name, accent, logo}."""
        self._tabs = list(tabs)
        self._logo_cache = {}
        for t in self._tabs:
            self._logo_cache[t["id"]] = self._load_logo(t.get("logo"))
        if self._active is None and self._tabs:
            self._active = self._tabs[0]["id"]
        self._indicator_y = self._slot_center_y(self._active)
        self.update()

    def set_active(self, source_id, animate=True):
        if source_id != "settings" and source_id not in [t["id"] for t in self._tabs]:
            return
        self._active = source_id
        target = self._slot_center_y(source_id)
        if animate:
            self._ind_anim.stop()
            self._ind_anim.setStartValue(self._indicator_y)
            self._ind_anim.setEndValue(target)
            self._ind_anim.start()
        else:
            self._indicator_y = target
        self.update()

    def set_status(self, source_id, severity):
        if self._status.get(source_id) != severity:
            self._status[source_id] = severity
            self.update()

    # ---- indicator animation property ----

    def _get_indicator_y(self):
        return self._indicator_y

    def _set_indicator_y(self, v):
        self._indicator_y = v
        self.update()

    indicator_y = Property(float, _get_indicator_y, _set_indicator_y)

    # ---- geometry ----

    def _slot_rect(self, i):
        x = (self.width() - SLOT) / 2
        y = TOP + i * (SLOT + GAP)
        return QRectF(x, y, SLOT, SLOT)

    def _slot_center_y(self, source_id):
        if source_id == "settings":
            return self._settings_rect().center().y()
        for i, t in enumerate(self._tabs):
            if t["id"] == source_id:
                return self._slot_rect(i).center().y()
        return float(TOP + SLOT / 2)

    def _refresh_rect(self):
        x = (self.width() - SLOT) / 2
        return QRectF(x, self.height() - 14 - SLOT * 2 - GAP, SLOT, SLOT)

    def _settings_rect(self):
        x = (self.width() - SLOT) / 2
        return QRectF(x, self.height() - 14 - SLOT, SLOT, SLOT)

    # ---- logos ----

    def _load_logo(self, path):
        if not path or not _HAVE_SVG or not os.path.exists(path):
            return None
        try:
            r = QSvgRenderer(path)
            pm = QPixmap(64, 64)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            r.render(p)
            p.end()
            return pm
        except Exception:
            return None

    # ---- pulse ----

    def _tick_pulse(self):
        if not self.isVisible() or self._active is None:
            return
        self._pulse_phase = (self._pulse_phase + 0.06) % (2 * math.pi)
        self.update()

    # ---- mouse ----

    def _hit(self, pos):
        for i, t in enumerate(self._tabs):
            if self._slot_rect(i).contains(pos):
                return t["id"]
        if self._refresh_rect().contains(pos):
            return "refresh"
        if self._settings_rect().contains(pos):
            return "settings"
        return None

    def mouseMoveEvent(self, event):
        h = self._hit(event.position())
        if h != self._hover:
            self._hover = h
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor if h
                                   else Qt.CursorShape.ArrowCursor))
            self.update()

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.unsetCursor()
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        h = self._hit(event.position())
        if h == "refresh":
            self.refresh_clicked.emit()
        elif h == "settings":
            self.source_selected.emit("settings")  # Settings is its own tab
        elif h:
            self.source_selected.emit(h)

    # ---- paint ----

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # rail background — round the left corners to follow the window shape
        R = 11
        corner = QPainterPath()
        corner.addRoundedRect(QRectF(0, 0, self.width() + R, self.height()), R, R)
        p.setClipPath(corner)
        p.fillRect(self.rect(), Colors.RAIL_BG)
        p.setClipping(False)
        # right hairline
        p.setPen(QPen(Colors.HAIRLINE, 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

        self._paint_pulse_mark(p)

        active_accent = self._accent_of(self._active)
        # active indicator bar (left edge)
        if self._active is not None:
            bar_h = SLOT - 10
            by = self._indicator_y - bar_h / 2
            bar = QPainterPath()
            bar.addRoundedRect(QRectF(2, by, 3, bar_h), 1.5, 1.5)
            p.fillPath(bar, QBrush(QColor(active_accent)))

        for i, t in enumerate(self._tabs):
            self._paint_slot(p, i, t)

        self._paint_action(p, self._refresh_rect(), "↻", self._hover == "refresh")
        self._paint_settings(p)
        p.end()

    def _paint_settings(self, p):
        rect = self._settings_rect()
        active = (self._active == "settings")
        hover = (self._hover == "settings")
        accent = QColor(self._settings_accent)
        if active:
            glow = 0.5 + 0.5 * math.sin(self._pulse_phase)
            wash = QColor(accent)
            wash.setAlpha(int(28 + 16 * glow))
            path = QPainterPath()
            path.addRoundedRect(rect, 12, 12)
            p.fillPath(path, QBrush(wash))
            p.setPen(QPen(accent, 1))
            p.drawPath(path)
        elif hover:
            path = QPainterPath()
            path.addRoundedRect(rect, 12, 12)
            p.fillPath(path, QBrush(Colors.SURFACE))
        pm = self._settings_logo
        if pm is not None and not pm.isNull():
            target = rect.adjusted(11, 11, -11, -11)
            p.setOpacity(1.0 if active else (0.72 if hover else 0.5))
            p.drawPixmap(target.toRect(), pm)
            p.setOpacity(1.0)
        else:
            p.setPen(accent if active else Colors.TEXT_MUTED)
            f = QFont("Segoe UI", 15)
            p.setFont(f)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "⚙")

    def _accent_of(self, source_id):
        if source_id == "settings":
            return QColor(self._settings_accent)
        for t in self._tabs:
            if t["id"] == source_id:
                return QColor(t.get("accent", "#7C83FF"))
        return QColor("#7C83FF")

    def _paint_pulse_mark(self, p):
        # a small heartbeat line in the brand cyan→magenta
        cx = self.width() / 2
        p.setPen(QPen(Colors.CYAN, 2, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        y = 28
        pts = [(cx - 14, y), (cx - 6, y), (cx - 2, y - 8),
               (cx + 3, y + 9), (cx + 7, y), (cx + 14, y)]
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF as _QPF
        p.drawPolyline(QPolygonF([_QPF(x, yy) for x, yy in pts]))

    def _paint_slot(self, p, i, t):
        rect = self._slot_rect(i)
        sid = t["id"]
        accent = QColor(t.get("accent", "#7C83FF"))
        active = (sid == self._active)
        hover = (sid == self._hover)

        # slot background
        if active:
            glow = 0.5 + 0.5 * math.sin(self._pulse_phase)
            wash = QColor(accent)
            wash.setAlpha(int(28 + 16 * glow))
            path = QPainterPath()
            path.addRoundedRect(rect, 12, 12)
            p.fillPath(path, QBrush(wash))
            p.setPen(QPen(accent, 1))
            p.drawPath(path)
        elif hover:
            path = QPainterPath()
            path.addRoundedRect(rect, 12, 12)
            p.fillPath(path, QBrush(Colors.SURFACE))

        # logo or monogram
        pm = self._logo_cache.get(sid)
        if pm is not None and not pm.isNull():
            target = rect.adjusted(11, 11, -11, -11)
            p.setOpacity(1.0 if active else 0.5)
            p.drawPixmap(target.toRect(), pm)
            p.setOpacity(1.0)
        else:
            p.setPen(accent if active else Colors.TEXT_MUTED)
            f = QFont("Segoe UI", 11)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, _mono(sid, t.get("name", "")))

        # status dot (top-right)
        sev = self._status.get(sid, "normal")
        dot = Colors.severity_color(sev)
        dr = 4.0
        dc = QPointF(rect.right() - 6, rect.top() + 6)
        p.setPen(QPen(Colors.RAIL_BG, 2))
        p.setBrush(QBrush(dot))
        p.drawEllipse(dc, dr, dr)

    def _paint_action(self, p, rect, glyph, hover):
        if hover:
            path = QPainterPath()
            path.addRoundedRect(rect, 12, 12)
            p.fillPath(path, QBrush(Colors.SURFACE))
        p.setPen(Colors.TEXT_SECONDARY if hover else Colors.TEXT_MUTED)
        f = QFont("Segoe UI", 15)
        p.setFont(f)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
