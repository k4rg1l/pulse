"""Runtime accent controller for per-source theming (UI overhaul).

A single, module-level "active accent" that the dashboard drives on every tab
switch. Painted widgets that should theme to the active source read
`theme_controller.accent()` in their paintEvent and connect `changed` to their
own `update()` so they repaint as the accent tweens.

Severity colors (credit_color, temp color, usage severity, …) are deliberately
NOT routed through here — danger signals must never be tinted by identity.

Main-thread only (the animation runs on the GUI event loop; the GC discipline
is unaffected).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor


class _AccentBus(QObject):
    changed = Signal()


_bus = _AccentBus()
#: connect this to a widget's update() to repaint it as the accent animates
changed = _bus.changed

_accent = QColor("#7C83FF")
_anim = None  # created lazily once a QApplication/event loop exists


def accent() -> QColor:
    """Current accent as a fresh QColor (safe to mutate by the caller)."""
    return QColor(_accent)


def accent_hex() -> str:
    return _accent.name()


def _apply(c) -> None:
    global _accent
    _accent = QColor(c)
    _bus.changed.emit()


def set_accent(color, animate: bool = True) -> None:
    """Set the active accent. When `animate`, tween from the current color
    (≈260ms OutCubic); otherwise snap instantly."""
    global _anim
    target = QColor(color)
    if not animate or QColor(_accent) == target:
        _apply(target)
        return
    if _anim is None:
        _anim = QVariantAnimation(_bus)
        _anim.setDuration(260)
        _anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        _anim.valueChanged.connect(
            lambda v: _apply(v) if isinstance(v, QColor) else None
        )
    _anim.stop()
    _anim.setStartValue(QColor(_accent))
    _anim.setEndValue(target)
    _anim.start()
