"""Small reusable animation helpers for the UI overhaul.

Main-thread only — Qt animations run on the GUI event loop; never invoke these
from a worker thread, so the gc.disable() discipline is unaffected.

A module-level `ANIMATIONS_ON` flag (driven from settings.enable_animations)
lets callers short-circuit to instant updates.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QPropertyAnimation, QVariantAnimation, QEasingCurve, QParallelAnimationGroup,
)

ANIMATIONS_ON = True


def set_enabled(enabled: bool) -> None:
    global ANIMATIONS_ON
    ANIMATIONS_ON = bool(enabled)


def fade(effect, frm, to, ms=200, easing=QEasingCurve.Type.OutCubic):
    """Animate a QGraphicsOpacityEffect's opacity frm→to. Returns the
    QPropertyAnimation (caller keeps a reference + starts it)."""
    a = QPropertyAnimation(effect, b"opacity")
    a.setDuration(ms)
    a.setStartValue(float(frm))
    a.setEndValue(float(to))
    a.setEasingCurve(easing)
    return a


def slide(widget, prop, frm, to, ms=220, easing=QEasingCurve.Type.OutCubic):
    """Animate a numeric Qt property (e.g. b"x_offset") frm→to."""
    a = QPropertyAnimation(widget, prop)
    a.setDuration(ms)
    a.setStartValue(frm)
    a.setEndValue(to)
    a.setEasingCurve(easing)
    return a


def count_up(setter, frm, to, ms=600, easing=QEasingCurve.Type.OutCubic):
    """Drive `setter(float)` from frm→to. If animations are off, calls the
    setter once with `to`. Returns the animation (or None when instant)."""
    if not ANIMATIONS_ON or float(frm) == float(to):
        setter(float(to))
        return None
    a = QVariantAnimation()
    a.setDuration(ms)
    a.setStartValue(float(frm))
    a.setEndValue(float(to))
    a.setEasingCurve(easing)
    a.valueChanged.connect(lambda v: setter(float(v)))
    return a


def parallel(*animations):
    """Group several animations to run together. Returns the group."""
    g = QParallelAnimationGroup()
    for a in animations:
        if a is not None:
            g.addAnimation(a)
    return g
