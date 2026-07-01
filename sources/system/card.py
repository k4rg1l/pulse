"""The System card (main-thread QWidget): CPU + RAM bars and net up/down.

Font-metric-driven geometry (shared _build_ops) like the GPU/Claude cards, so
content can't clip. Renders a SystemStats, or an "unavailable" line on None.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter

from theme import Colors, Fonts
import theme_controller

from sources.base_card import BaseCard


def _fmt_rate(bps: float) -> str:
    if bps >= 1e6:
        return f"{bps / 1e6:.1f} MB/s"
    if bps >= 1e3:
        return f"{bps / 1e3:.0f} KB/s"
    return f"{bps:.0f} B/s"


class SystemCard(BaseCard):

    def _build_ops(self):
        ops = []
        y = self.PAD_Y
        # Header band ("SYSTEM") so the first metric starts at the same y as the
        # GPU/Claude tabs (which both reserve a header) — consistent top rhythm.
        ops.append(("header", y))
        y += self._fm_h(Fonts.label()) + self.GAP_HEADER
        if self._data is None:
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
        self._paint_chrome(p, w, h)

        s = self._data
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
