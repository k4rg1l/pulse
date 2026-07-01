"""Shared base for the source cards (GPU / System today; Claude to follow).

Owns the house chrome (rounded BG_CARD + border), the font-metric height
plumbing (``render(data)`` sizes the card from ``_build_ops()`` so content can
never clip), and the label + value + progress-bar row. A subclass provides
``_build_ops()`` (the measured ``(kind, y)`` op list) and a ``paintEvent`` that
paints the chrome via ``_paint_chrome()`` then draws each op.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QBrush, QFontMetrics, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from theme import Colors, Fonts
import theme_controller


class BaseCard(QWidget):
    PAD_X = 14
    PAD_Y = 12
    BAR_H = 6
    GAP_HEADER = 10
    GAP_LABEL_BAR = 4
    GAP_ROW = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(96)
        theme_controller.changed.connect(self.update)

    def render(self, data):
        self._data = data
        _, total = self._build_ops()
        self.setFixedHeight(int(total))
        self.update()

    def _build_ops(self):
        raise NotImplementedError

    @staticmethod
    def _fm_h(font):
        return QFontMetrics(font).height()

    def _paint_chrome(self, p, w, h):
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        p.fillPath(path, QBrush(Colors.BG_CARD))
        p.setPen(QPen(Colors.BORDER, 1))
        p.drawPath(path)

    def _bar_row(self, p, x, y, w, label, value, frac, color):
        right = w - self.PAD_X
        lh = self._fm_h(Fonts.body())
        p.setPen(Colors.TEXT_SECONDARY)
        p.setFont(Fonts.body())
        p.drawText(QRectF(x, y, 150, lh),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        p.setPen(color)
        p.setFont(Fonts.mono_small())
        p.drawText(QRectF(right - 180, y, 180, lh),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, value)
        bar_y = y + lh + self.GAP_LABEL_BAR
        bar_w = right - x
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(x, bar_y, bar_w, self.BAR_H), 3, 3)
        p.fillPath(bg, QBrush(Colors.BORDER))
        fill = max(0.0, min(1.0, frac)) * bar_w
        if fill > 1:
            fp = QPainterPath()
            fp.addRoundedRect(QRectF(x, bar_y, fill, self.BAR_H), 3, 3)
            p.fillPath(fp, QBrush(color))
