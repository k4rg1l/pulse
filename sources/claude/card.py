"""The Claude usage + token card (main-thread QWidget).

Renders a ClaudeCardData: per-window utilization bars (5h / 7d / Sonnet) with
reset countdowns and severity colour, plus a local 7-day token-accounting
footer. Painted in Pulse's house style (rounded BG_CARD).

Geometry is driven by real font metrics via a single `_build_ops()` source of
truth shared by paintEvent and render(), so the computed height can never
drift from what's painted (a hardcoded-height version clipped the last line).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QBrush, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from theme import Colors, Fonts
import theme_controller


def _fmt_tokens(n) -> str:
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}k"
    return str(n)


def _short_model(mid: str) -> str:
    m = (mid or "").lower()
    fam = ("Opus" if "opus" in m else "Sonnet" if "sonnet" in m
           else "Haiku" if "haiku" in m else "Fable" if "fable" in m else None)
    if fam:
        ver = re.search(r"(\d+)[-.](\d+)", m)
        return f"{fam} {ver.group(1)}.{ver.group(2)}" if ver else fam
    return mid


def _fmt_reset(dt) -> str:
    if dt is None:
        return ""
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    if delta <= 0:
        return "resetting"
    d = int(delta // 86400)
    h = int((delta % 86400) // 3600)
    m = int((delta % 3600) // 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _fmt_age(seconds) -> str:
    """Human 'as of …' age for cached usage."""
    s = int(max(0, seconds or 0))
    if s < 45:
        return "just now"
    m = s // 60
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m}m ago"
    h = m // 60
    if h < 24:
        return f"{h}h ago"
    return f"{h // 24}d ago"


def _sev_color(sev: str):
    """Map a usage severity to its bar/text colour. Normal reads in the active
    source accent (identity); severity still wins for warning/critical."""
    if sev == "critical":
        return Colors.RED
    if sev == "warning":
        return Colors.YELLOW
    return theme_controller.accent()


class ClaudeCard(QWidget):
    PAD_X = 14
    PAD_Y = 12
    BAR_H = 6
    # vertical gaps (px)
    GAP_HEADER = 10     # below the tier header
    GAP_LABEL_BAR = 4   # between a window's label and its bar
    GAP_ROW = 10        # below a window's bar (before the next row)
    GAP_MSG = 12        # below the stale/empty message
    GAP_DIV = 6         # around the footer divider
    GAP_FOOT = 4        # between footer lines

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

    # ---- geometry (single source of truth for paint + height) ----

    def _windows(self):
        d = self._data
        return d.usage.windows if (d and d.usage and d.usage.windows) else []

    @staticmethod
    def _fm_h(font):
        return QFontMetrics(font).height()

    def _build_ops(self):
        """Return (ops, total_height). `ops` is a list of (kind, y, *extra)
        drawn top-down using measured font heights; total_height is where the
        content ends plus bottom padding. Both paintEvent and render() use
        this, so the height always matches the painted content."""
        d = self._data
        ops = []
        y = self.PAD_Y

        ops.append(("header", y))
        y += self._fm_h(Fonts.label()) + self.GAP_HEADER

        wins = self._windows()
        if wins:
            for w in wins:
                ops.append(("window", y, w))
                y += self._fm_h(Fonts.body()) + self.GAP_LABEL_BAR + self.BAR_H + self.GAP_ROW
        else:
            ops.append(("message", y))
            y += self._fm_h(Fonts.body()) + self.GAP_MSG

        if d and d.tokens and d.tokens.messages:
            y += self.GAP_DIV
            ops.append(("divider", y))
            y += self.GAP_DIV
            ops.append(("foot_label", y))
            y += self._fm_h(Fonts.label()) + self.GAP_FOOT
            ops.append(("foot_tokens", y))
            y += self._fm_h(Fonts.body()) + self.GAP_FOOT
            ops.append(("foot_split", y))
            y += self._fm_h(Fonts.tiny())

        return ops, y + self.PAD_Y

    # ---- paint ----

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

        d = self._data
        x = self.PAD_X
        right = w - self.PAD_X
        ops, _ = self._build_ops()

        for op in ops:
            kind, y = op[0], op[1]
            if kind == "header":
                tier = d.subscription.upper() if (d and d.subscription) else "CLAUDE"
                lh = self._fm_h(Fonts.label())
                p.setPen(Colors.TEXT_SECONDARY)
                p.setFont(Fonts.label())
                p.drawText(QRectF(x, y, right - x, lh),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, tier)
                note, col = (None, Colors.TEXT_MUTED)
                st = getattr(d, "usage_status", "unavailable") if d else "unavailable"
                if st == "expired":
                    note, col = "open Claude Code", Colors.YELLOW
                elif st in ("live", "cached") and d and d.usage_age_seconds is not None:
                    note, col = f"as of {_fmt_age(d.usage_age_seconds)}", Colors.TEXT_MUTED
                elif st == "unavailable" and d and d.error:
                    note, col = "unavailable", Colors.RED
                if note:
                    p.setPen(col)
                    p.setFont(Fonts.tiny())
                    p.drawText(QRectF(x, y, right - x, lh),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, note)
            elif kind == "window":
                self._paint_window(p, op[2], x, y, w)
            elif kind == "message":
                st = getattr(d, "usage_status", "unavailable") if d else "unavailable"
                if st == "expired":
                    msg = "Open Claude Code to refresh usage"
                elif d and d.error:
                    msg = d.error
                else:
                    msg = "No usage data yet"
                p.setPen(Colors.TEXT_MUTED)
                p.setFont(Fonts.body())
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.body())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, msg)
            elif kind == "divider":
                p.setPen(QPen(Colors.BORDER, 1))
                p.drawLine(int(x), int(y), int(right), int(y))
            elif kind == "foot_label":
                p.setPen(Colors.TEXT_MUTED)
                p.setFont(Fonts.label())
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.label())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "LAST 7 DAYS")
            elif kind == "foot_tokens":
                t = d.tokens
                p.setPen(Colors.TEXT_PRIMARY)
                p.setFont(Fonts.body())
                line = (f"{_fmt_tokens(t.total)} tokens · {t.cache_efficiency * 100:.0f}% cached "
                        f"· {t.messages:,} msgs")
                p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.body())),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, line)
            elif kind == "foot_split":
                t = d.tokens
                if t.by_model and t.total:
                    items = sorted(t.by_model.items(), key=lambda kv: -kv[1])[:2]
                    parts = [f"{_short_model(m)} {v * 100 // t.total}%" for m, v in items]
                    p.setPen(Colors.TEXT_SECONDARY)
                    p.setFont(Fonts.tiny())
                    p.drawText(QRectF(x, y, right - x, self._fm_h(Fonts.tiny())),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                               "   ·   ".join(parts))
        p.end()

    def _paint_window(self, p, win, x, y, w):
        right = w - self.PAD_X
        color = _sev_color(win.severity)
        lh = self._fm_h(Fonts.body())

        p.setPen(Colors.TEXT_SECONDARY)
        p.setFont(Fonts.body())
        p.drawText(QRectF(x, y, 150, lh),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, win.label)

        reset = _fmt_reset(win.resets_at)
        txt = f"{win.utilization:.0f}%" + (f"  ·  resets {reset}" if reset else "")
        p.setPen(color)
        p.setFont(Fonts.mono_small())
        p.drawText(QRectF(right - 180, y, 180, lh),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, txt)

        bar_y = y + lh + self.GAP_LABEL_BAR
        bar_w = right - x
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(x, bar_y, bar_w, self.BAR_H), 3, 3)
        p.fillPath(bg, QBrush(Colors.BORDER))
        fill_w = max(0.0, min(1.0, win.utilization / 100.0)) * bar_w
        if fill_w > 1:
            fp = QPainterPath()
            fp.addRoundedRect(QRectF(x, bar_y, fill_w, self.BAR_H), 3, 3)
            p.fillPath(fp, QBrush(color))
