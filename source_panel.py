"""A source's full panel: a themed header (logo + name + meta + actions) over
the source's content body, optionally scrollable (the OpenRouter panel is the
only content-heavy one). Source-agnostic — the dashboard wraps each source's
card/content in one of these and adds it to the panel stack.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import (
    QColor, QCursor, QPainter, QPixmap, QBrush, QLinearGradient, QRadialGradient,
    QPainterPath,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame, QSizePolicy,
)

import theme_controller
from theme import Colors, Fonts

try:
    from PySide6.QtSvg import QSvgRenderer
    _HAVE_SVG = True
except Exception:  # pragma: no cover
    _HAVE_SVG = False

HEADER_H = 56

_BTN_CSS = """
QPushButton {
    background: #1a1a2e; color: #a0a0c8; border: 1px solid #26263f;
    border-radius: 7px; font-size: 14px; font-weight: bold;
}
QPushButton:hover { background: #23233e; color: #ECECF7; border-color: #3a3a60; }
QPushButton:pressed { background: #16162a; }
"""


def _logo_pixmap(logo_path, size=20):
    if not logo_path or not _HAVE_SVG or not os.path.exists(logo_path):
        return None
    try:
        r = QSvgRenderer(logo_path)
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        r.render(p)
        p.end()
        return pm
    except Exception:
        return None


class SourcePanel(QWidget):
    refresh_clicked = Signal()
    close_clicked = Signal()
    scrolled = Signal()

    def __init__(self, source_id, name, accent, logo, body,
                 scrollable=False, parent=None):
        super().__init__(parent)
        self.source_id = source_id
        self._accent = QColor(accent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header(name, logo))

        if scrollable:
            self._scroll = QScrollArea(self)
            self._scroll.setWidgetResizable(True)
            self._scroll.setFrameShape(QFrame.Shape.NoFrame)
            self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._scroll.setStyleSheet("background: transparent; border: none;")
            holder = QWidget()
            holder.setStyleSheet("background: transparent;")
            hv = QVBoxLayout(holder)
            # Match the non-scrollable wrap (16/16) so the first card sits the
            # same distance below the header on every panel, and top==bottom.
            hv.setContentsMargins(20, 16, 20, 16)
            hv.setSpacing(12)
            hv.addWidget(body)
            hv.addStretch()
            self._scroll.setWidget(holder)
            self._scroll.verticalScrollBar().valueChanged.connect(self.scrolled)
            root.addWidget(self._scroll, 1)
        else:
            self._scroll = None
            wrap = QWidget(self)
            wrap.setStyleSheet("background: transparent;")
            wv = QVBoxLayout(wrap)
            wv.setContentsMargins(20, 16, 20, 16)
            wv.setSpacing(12)
            wv.addWidget(body)
            wv.addStretch()
            root.addWidget(wrap, 1)

        # repaint the accent underline as the active accent tweens
        theme_controller.changed.connect(self.update)

    def _build_header(self, name, logo):
        header = QWidget(self)
        header.setFixedHeight(HEADER_H)
        header.setStyleSheet("background: transparent;")
        h = QHBoxLayout(header)
        # Right inset matches the left (20) and the body content right edge so the
        # refresh/close cluster doesn't overhang the card column.
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(10)

        self._logo = QLabel(header)
        pm = _logo_pixmap(logo, 22)
        if pm is not None:
            self._logo.setPixmap(pm.scaled(22, 22, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
        else:
            self._logo.setText("")  # monogram drawn by paintEvent fallback could go here
            self._logo.setFixedWidth(0)
        h.addWidget(self._logo)

        self._name = QLabel(name, header)
        self._name.setFont(Fonts.panel_title())
        self._name.setStyleSheet("color: #ECECF7;")
        h.addWidget(self._name)

        h.addStretch()

        self._meta = QLabel("", header)
        self._meta.setFont(Fonts.meta())
        self._meta.setStyleSheet("color: #8A8AAE;")
        h.addWidget(self._meta)

        refresh = QPushButton("↻", header)
        refresh.setFixedSize(28, 28)
        refresh.setStyleSheet(_BTN_CSS)
        refresh.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        refresh.clicked.connect(self.refresh_clicked)
        h.addWidget(refresh)

        close = QPushButton("✕", header)
        close.setFixedSize(28, 28)
        close.setStyleSheet(_BTN_CSS)
        close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close.clicked.connect(self.close_clicked)
        h.addWidget(close)

        self._header = header
        return header

    def set_meta(self, text):
        self._meta.setText(text or "")

    def reset_scroll(self):
        if self._scroll is not None:
            self._scroll.verticalScrollBar().setValue(0)

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        accent = theme_controller.accent()

        # Clip the right corners to the window's rounded shape so the panel
        # background never bleeds square past the container border.
        R = 11
        corner = QPainterPath()
        corner.addRoundedRect(QRectF(-R, 0, w + R, h), R, R)
        p.setClipPath(corner)

        # Base: a vertical depth gradient so the panel never reads as a flat,
        # dead rectangle.
        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0.0, QColor(23, 23, 38))
        base.setColorAt(0.55, QColor(16, 16, 27))
        base.setColorAt(1.0, QColor(11, 11, 19))
        p.fillRect(self.rect(), QBrush(base))

        # Accent HUE glow — CONFINED to the header band so it stops exactly at
        # the header's bottom border instead of bleeding into the content.
        p.setClipRect(QRectF(0, 0, w, HEADER_H))
        glow = QRadialGradient(w * 0.42, -6, w * 0.9)
        g0 = QColor(accent); g0.setAlpha(88)
        g1 = QColor(accent); g1.setAlpha(0)
        glow.setColorAt(0.0, g0)
        glow.setColorAt(1.0, g1)
        p.fillRect(QRectF(0, 0, w, HEADER_H), QBrush(glow))
        p.setClipping(False)

        # Crisp accent underline exactly at the header border.
        line = QColor(accent); line.setAlpha(235)
        p.fillRect(QRectF(0, HEADER_H - 1.5, w, 1.6), line)
        p.end()
