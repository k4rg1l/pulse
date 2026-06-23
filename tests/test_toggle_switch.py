"""Regression tests for settings_panel.ToggleSwitch.

Caught in the wild 2026-06-23: the animated property was named `pos`, which
collides with QWidget's built-in geometry `pos` property. The
QPropertyAnimation then drove the WIDGET'S POSITION to (0,0) instead of the
knob, flinging every toggle to its row's top-left (on top of the label) the
moment it was clicked. These tests lock in the fix (property renamed to
`knob`) by directly measuring widget geometry — no flaky timing.
"""
from PySide6.QtCore import QPoint

from settings_panel import ToggleSwitch


def test_widget_pos_is_not_shadowed(qapp):
    """QWidget.pos() must remain the geometry accessor. If a float Property
    were named 'pos', calling sw.pos() would raise 'float object is not
    callable'."""
    sw = ToggleSwitch(on=True)
    sw.move(120, 40)
    assert sw.pos() == QPoint(120, 40)


def test_toggling_does_not_move_the_widget(qapp):
    """The bug: flipping the switch moved the whole widget to (0,0). It must
    only change the knob state, never the widget's position."""
    sw = ToggleSwitch(on=True)
    sw.move(200, 10)
    before = sw.pos()
    sw.setChecked(False, animate=False)
    assert sw._pos == 0.0          # knob state flipped
    assert sw.pos() == before      # widget stayed put
    sw.setChecked(True, animate=False)
    assert sw._pos == 1.0
    assert sw.pos() == before


def test_knob_property_drives_internal_position(qapp):
    """The animated property is 'knob' and writes the internal knob position."""
    sw = ToggleSwitch(on=False)
    sw.setProperty("knob", 1.0)
    assert sw._pos == 1.0
