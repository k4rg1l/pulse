"""Unit tests for the global-hotkey spec parser (pure). The Win32 listener
itself is validated live (simulated keypress -> dashboard) per docs/TESTING.md.
"""
from hotkey import (
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, parse_hotkey,
)


def test_parses_win_shift_o():
    mods, vk = parse_hotkey("win+shift+o")
    assert mods == (MOD_WIN | MOD_SHIFT | MOD_NOREPEAT)
    assert vk == ord("O")


def test_ctrl_alt_letter():
    mods, vk = parse_hotkey("ctrl+alt+p")
    assert mods == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT)
    assert vk == ord("P")


def test_function_key():
    _, vk = parse_hotkey("ctrl+f5")
    assert vk == 0x70 + 4  # VK_F5


def test_case_insensitive():
    assert parse_hotkey("WIN+Shift+O") == parse_hotkey("win+shift+o")


def test_requires_modifier_and_key():
    assert parse_hotkey("o") is None              # no modifier
    assert parse_hotkey("win") is None            # no key
    assert parse_hotkey("") is None
    assert parse_hotkey("win+shift+notakey") is None
