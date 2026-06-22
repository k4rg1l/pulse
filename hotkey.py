"""Global hotkey to summon the dashboard from anywhere.

Qt's QShortcut/QKeySequence can NOT be used here: the dashboard uses
BypassWindowManagerHint and never holds focus, so a focus-scoped shortcut
never fires (see AGENTS.md). Instead we use the Win32 RegisterHotKey API on a
dedicated thread running a GetMessage loop; on WM_HOTKEY we emit a Qt signal
that the controller connects to show the dashboard.

We deliberately use RegisterHotKey (a benign, system-registered hotkey) rather
than a low-level WH_KEYBOARD_LL hook — the latter is structurally a keylogger
and trips AV on the unsigned build. RegisterHotKey uses no callback, so there
is no ctypes-trampoline lifetime hazard.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODS = {
    "alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT, "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN,
}


def parse_hotkey(spec: str) -> Optional[Tuple[int, int]]:
    """'win+shift+o' -> (modifiers, virtual-key) or None if unparseable.

    Requires at least one modifier plus one key (a single character or Fn).
    Pure — unit-tested.
    """
    if not spec:
        return None
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        else:
            key = p
    if not key or mods == 0:
        return None
    if len(key) == 1:
        vk = ord(key.upper())
    elif key[0] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1   # VK_F1 == 0x70
    else:
        return None
    return mods | MOD_NOREPEAT, vk


class HotkeyListener(QObject):
    """Registers a global hotkey on a dedicated Win32 message-loop thread and
    emits `summon` on the main thread when it fires."""

    summon = Signal()

    def __init__(self, spec: str = "win+shift+o", parent=None):
        super().__init__(parent)
        self._parsed = parse_hotkey(spec)
        self._spec = spec
        self._thread: Optional[threading.Thread] = None
        self._tid: Optional[int] = None
        self.active = False

    def start(self) -> bool:
        if self._parsed is None:
            return False
        self._thread = threading.Thread(target=self._run, name="pulse-hotkey", daemon=True)
        self._thread.start()
        return True

    def _run(self):
        user32 = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        mods, vk = self._parsed
        if not user32.RegisterHotKey(None, 1, mods, vk):
            print(f"[hotkey] RegisterHotKey failed for '{self._spec}' (already in use?)")
            return
        self.active = True
        msg = wintypes.MSG()
        try:
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):   # WM_QUIT or error
                    break
                if msg.message == WM_HOTKEY:
                    self.summon.emit()
        finally:
            user32.UnregisterHotKey(None, 1)
            self.active = False

    def stop(self):
        if self._tid is not None:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
