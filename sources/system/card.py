"""The System card (main-thread QWidget): CPU + RAM bars and net up/down.

Font-metric-driven geometry (shared _build_ops) like the GPU/Claude cards, so
content can't clip. Renders a SystemStats, or an "unavailable" line on None.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QBrush, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from theme import Colors, Fonts
import theme_controller


def _fmt_rate(bps: float) -> str:
    if bps >= 1e6:
        return f"{bps / 1e6:.1f} MB/s"
    if bps >= 1e3:
        return f"{bps / 1e3:.0f} KB/s"
    return f"{bps:.0f} B/s"


class SystemCard(QWidget):
    PAD_X = 14
    PAD_Y = 12
    BAR_H = 6
    GAP_HEADER = 10
    GAP_LABEL_BAR = 4
    GAP_ROW = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stats = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(96)
        theme_controller.changed.connect(self.update)

    def render(self, stats):
        self._stats = stats
        _, total = self._build_ops()
        self.setFixedHeight(int(total))
        self.update()

    @staticmethod
    def _fm_h(font):
        return QFontMetrics(font).height()

    def _build_ops(self):
        ops = []
        y = self.PAD_Y
        # Header band ("SYSTEM") so the first metric starts at the same y as the
        # GPU/Claude tabs (which both reserve a header) — consistent top rhythm.
        ops.append(("header", y))
        y += self._fm_h(Fonts.label()) + self.GAP_HEADER
        if self._stats is None:
            ops.append(("message", y))
            y += self._fm_h(Fonts.body())
            return ops, y + self.PAD_Y
        for kind in ("cpu", "ram"):
            ops.append((kind, y))
            y += self._fm_h(Fonts.body()) + self.GAP_LABEL_BAR + self.BAR_H + self.GAP_ROW
        ops.append(("net", y))
        y += self._fm_h(Fonts.body())
        return ops, y + self.PAD_Y

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        p.fillPath(path, QBrush(Colors.BG_CARD))
        p.setPen(QPen(Colors.BORDER, 1))
        p.drawPath(path)

        s = self._stats
        x, right = self.PAD_X, w - self.PAD_X
        for kind, y in self._build_ops()[0]:
            if kind == "header":
                p.setPen(Colors.TEXT_SECONDARY)
                p.setFont(Fonts.label())
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.label())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "SYSTEM")
            elif kind == "message":
                p.setPen(Colors.TEXT_MUTED)
                p.setFont(Fonts.body())
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.body())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           "System stats unavailable")
            elif kind == "cpu":
                self._bar_row(p, x, y, w, "CPU", f"{s.cpu:.0f}%", s.cpu / 100.0,
                              theme_controller.accent())
            elif kind == "ram":
                self._bar_row(p, x, y, w, "RAM",
                              f"{s.ram_used_gb:.1f} / {s.ram_total_gb:.1f} GB",
                              s.ram_percent / 100.0, theme_controller.accent())
            elif kind == "net":
                p.setFont(Fonts.body())
                p.setPen(Colors.GREEN)
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.body())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           f"↓ {_fmt_rate(s.net_down)}")
                p.setPen(Colors.CYAN)
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.body())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                           f"↑ {_fmt_rate(s.net_up)}")
        p.end()

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
