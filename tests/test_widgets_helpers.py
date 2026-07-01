"""Unit tests for module-level paint helpers in widgets.py.

Locks `_alpha` (the QColor+setAlpha DRY helper) — including that it does NOT
recurse into itself (a self-application regression during the DRY sweep) and does
NOT mutate its input.
"""
from theme import Colors
from widgets import _alpha


def test_alpha_sets_alpha_without_mutating_input():
    base = Colors.RED
    rgb = (base.red(), base.green(), base.blue())
    out = _alpha(base, 50)
    assert (out.red(), out.green(), out.blue(), out.alpha()) == (*rgb, 50)
    assert base.alpha() == 255            # input untouched


def test_alpha_accepts_hex_and_expression_alpha():
    assert _alpha("#00d2ff", 128).alpha() == 128
    # alpha may be an expression at call sites (e.g. `46 if hover else 26`)
    hover = True
    assert _alpha(Colors.CYAN, 46 if hover else 26).alpha() == 46
