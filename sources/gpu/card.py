"""The GPU card (main-thread QWidget): utilization + VRAM bars, temp, power.

Geometry is font-metric-driven (shared _build_ops for paint + height) — the
same approach as the Claude card, so the content can't clip. Renders a
GpuStats, or an "unavailable" line on None.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter

from theme import Colors, Fonts
import theme_controller

from sources.base_card import BaseCard


def _temp_color(t: int):
    if t >= 85:
        return Colors.RED
    if t >= 72:
        return Colors.YELLOW
    return Colors.GREEN


class GpuCard(BaseCard):

    def _build_ops(self):
        ops = []
        y = self.PAD_Y
        ops.append(("header", y))
        y += self._fm_h(Fonts.label()) + self.GAP_HEADER
        if self._data is None:
            ops.append(("message", y))
            y += self._fm_h(Fonts.body())
            return ops, y + self.PAD_Y
        for kind in ("util", "vram"):
            ops.append((kind, y))
            y += self._fm_h(Fonts.body()) + self.GAP_LABEL_BAR + self.BAR_H + self.GAP_ROW
        ops.append(("foot", y))
        y += self._fm_h(Fonts.body())
        return ops, y + self.PAD_Y

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        self._paint_chrome(p, w, h)

        s = self._data
        x, right = self.PAD_X, w - self.PAD_X
        for op in self._build_ops()[0]:
            kind, y = op
            if kind == "header":
                p.setPen(Colors.TEXT_SECONDARY)
                p.setFont(Fonts.label())
                name = s.short_name if s else "GPU"
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.label())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name.upper())
            elif kind == "message":
                p.setPen(Colors.TEXT_MUTED)
                p.setFont(Fonts.body())
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.body())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           "No NVIDIA GPU detected")
            elif kind == "util":
                self._bar_row(p, x, y, w, "Utilization", f"{s.util}%",
                              s.util / 100.0, theme_controller.accent())
            elif kind == "vram":
                self._bar_row(p, x, y, w, "VRAM",
                              f"{s.mem_used_gb:.1f} / {s.mem_total_gb:.1f} GB",
                              s.mem_percent / 100.0, theme_controller.accent())
            elif kind == "foot":
                p.setFont(Fonts.body())
                p.setPen(_temp_color(s.temp))
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.body())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           f"{s.temp}°C")
                if s.power is not None:
                    p.setPen(Colors.TEXT_SECONDARY)
                    p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.body())),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                               f"{s.power:.0f} W")
        p.end()
