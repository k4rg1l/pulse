"""The Settings tab — Pulse's own panel (not a source).

A clean, themed settings surface with hand-built animated toggle switches and a
segmented default-tab picker. Changes persist immediately; the ones that can
apply live (animations, click-away dismiss, default tab) do so through handler
callbacks, while source-visibility changes note that they apply on next launch.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import (
    Qt, Signal, QPropertyAnimation, QEasingCurve, Property, QRectF, QPointF,
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPainterPath, QCursor, QFont,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
)

import theme_controller
from theme import Colors, Fonts

log = logging.getLogger("pulse.settings_panel")


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


class ToggleSwitch(QWidget):
    """A small painted, animated on/off switch that themes to the active accent."""

    toggled = Signal(bool)

    def __init__(self, on=False, parent=None):
        super().__init__(parent)
        self._on = bool(on)
        self._pos = 1.0 if self._on else 0.0
        self.setFixedSize(42, 24)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        theme_controller.changed.connect(self.update)

    def get_pos(self):
        return self._pos

    def set_pos(self, v):
        self._pos = v
        self.update()

    pos = Property(float, get_pos, set_pos)

    def isChecked(self):
        return self._on

    def setChecked(self, on, animate=True):
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self._anim.stop()
        if animate:
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(1.0 if on else 0.0)
            self._anim.start()
        else:
            self._pos = 1.0 if on else 0.0
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._on)
            self.toggled.emit(self._on)

    def paintEvent(self, event):
        if self.width() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        accent = theme_controller.accent()

        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        col = _lerp(QColor(40, 40, 64), accent, self._pos)
        p.fillPath(track, QBrush(col))

        r = h / 2 - 3
        x0 = 3 + r
        x1 = w - 3 - r
        kx = x0 + (x1 - x0) * self._pos
        p.setBrush(QBrush(QColor(244, 244, 255)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(kx, h / 2), r, r)
        p.end()


class SettingsPanel(QWidget):
    """The Settings tab body. `tab_options` is a list of (id, display_name) for
    the default-tab picker; `handlers` is a dict of live-apply callbacks."""

    def __init__(self, settings, tab_options, handlers, parent=None):
        super().__init__(parent)
        self._s = settings
        self._h = handlers or {}
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        root.addWidget(self._section("Sources"))
        root.addWidget(self._toggle_row(
            "Show Claude", bool(getattr(settings, "show_claude", True)),
            lambda v: self._persist("show_claude", v)))
        root.addWidget(self._toggle_row(
            "Show GPU", bool(getattr(settings, "show_gpu", True)),
            lambda v: self._persist("show_gpu", v)))
        root.addWidget(self._toggle_row(
            "Show System", bool(getattr(settings, "show_system", True)),
            lambda v: self._persist("show_system", v)))
        root.addWidget(self._hint("Showing/hiding a source applies on next launch."))

        root.addSpacing(6)
        root.addWidget(self._section("Opens on"))
        root.addWidget(self._segmented(tab_options))

        root.addSpacing(6)
        root.addWidget(self._section("Behaviour"))
        root.addWidget(self._toggle_row(
            "Animations", bool(getattr(settings, "enable_animations", True)),
            lambda v: self._persist("enable_animations", v, "animations")))
        root.addWidget(self._toggle_row(
            "Close when you click away",
            bool(getattr(settings, "dismiss_on_focus_loss", True)),
            lambda v: self._persist("dismiss_on_focus_loss", v, "dismiss")))

        root.addSpacing(10)
        root.addWidget(self._divider())
        root.addWidget(self._info_row("Summon hotkey",
                                      getattr(settings, "hotkey", "win+shift+o") or "—"))

        open_btn = QPushButton("Open settings.json")
        open_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        open_btn.setStyleSheet(
            "QPushButton { background:#16162a; color:#9aa0ff; border:1px solid #2a2a44;"
            " border-radius:8px; padding:8px 12px; font-family:'Segoe UI'; font-size:9pt; }"
            "QPushButton:hover { background:#1d1d33; border-color:#3a3a60; color:#ECECF7; }")
        if "open_json" in self._h:
            open_btn.clicked.connect(self._h["open_json"])
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        row.addWidget(open_btn)
        row.addStretch()
        ver = QLabel("Pulse")
        ver.setFont(Fonts.meta())
        ver.setStyleSheet("color:#5a5a78;")
        row.addWidget(ver)
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        wrap.setLayout(row)
        root.addWidget(wrap)

    # ---- builders ----

    def _section(self, text):
        lbl = QLabel(text.upper())
        lbl.setFont(Fonts.label())
        lbl.setStyleSheet("color:#7a7a9a;")
        return lbl

    def _hint(self, text):
        lbl = QLabel(text)
        lbl.setFont(Fonts.meta())
        lbl.setStyleSheet("color:#5a5a78;")
        lbl.setWordWrap(True)
        return lbl

    def _divider(self):
        d = QWidget()
        d.setFixedHeight(1)
        d.setStyleSheet("background:#20203a;")
        return d

    def _toggle_row(self, label, on, on_change):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        lab = QLabel(label)
        lab.setFont(Fonts.body())
        lab.setStyleSheet("color:#c8c8e0;")
        h.addWidget(lab)
        h.addStretch()
        sw = ToggleSwitch(on)
        sw.toggled.connect(on_change)
        h.addWidget(sw)
        return w

    def _info_row(self, label, value):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        lab = QLabel(label)
        lab.setFont(Fonts.body())
        lab.setStyleSheet("color:#c8c8e0;")
        h.addWidget(lab)
        h.addStretch()
        val = QLabel(value)
        val.setFont(Fonts.mono_small())
        val.setStyleSheet("color:#8a8aae;")
        h.addWidget(val)
        return w

    def _segmented(self, tab_options):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        current = getattr(self._s, "default_source", "openrouter")
        self._seg_buttons = []
        for sid, name in tab_options:
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(sid == current)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setStyleSheet(
                "QPushButton { background:#16162a; color:#9a9ac0; border:1px solid #26263f;"
                " border-radius:8px; padding:6px 10px; font-family:'Segoe UI'; font-size:9pt; }"
                "QPushButton:checked { background:rgba(0,210,255,0.16); color:#ECECF7;"
                " border-color:#00d2ff; }")
            b.clicked.connect(lambda _=False, s=sid: self._set_default(s))
            self._seg_buttons.append((sid, b))
            h.addWidget(b)
        h.addStretch()
        return w

    # ---- actions ----

    def _persist(self, field, value, handler_key=None):
        setattr(self._s, field, value)
        self._save()
        if handler_key and handler_key in self._h:
            self._h[handler_key](value)

    def _set_default(self, sid):
        self._s.default_source = sid
        self._save()
        for s, b in self._seg_buttons:
            b.setChecked(s == sid)
        if "default_source" in self._h:
            self._h["default_source"](sid)

    def _save(self):
        try:
            self._s.save()
        except Exception:
            log.exception("settings save failed")
