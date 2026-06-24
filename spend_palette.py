"""Shared model→color palette for the Spend zone.

ONE source of truth so #9's spectrum bands and (later) #13's ghost chips assign
the SAME hue to the SAME model. A model's color is keyed by its descending-spend
RANK (rank 0 = heaviest spender), so the dominant model rhymes with the panel
accent and the assignment is stable across the 15-min polls as long as the spend
ordering holds. The model-id hash is a deterministic tiebreaker for the rare case
of two models at the same rank index (it nudges which ring slot a same-rank model
lands on so colors don't collide arbitrarily).

Kept dependency-light (theme only, no QWidget import) so both widgets.py and any
future consumer can import it without a cycle.
"""
from __future__ import annotations

import hashlib

from PySide6.QtGui import QColor

from theme import Colors
import theme_controller


def _ring() -> list[QColor]:
    """The deterministic color ring. Slot 0 is the live panel accent (the
    dominant model rhymes with the Credit Balance gauge above it); the rest are
    the house secondary hues. Rebuilt per call so a theme accent change is
    picked up (callers fetch this in set_data, never the paint hot path)."""
    return [
        theme_controller.accent(),  # dominant band == panel accent
        Colors.MAGENTA,
        Colors.CYAN,
        Colors.YELLOW,
        Colors.PURPLE,
    ]


def _hash_offset(model_id: str, modulo: int) -> int:
    """A stable 0..modulo-1 offset from the model id (deterministic across
    runs — uses hashlib, NOT the salted builtin hash())."""
    if modulo <= 0:
        return 0
    digest = hashlib.md5(model_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def model_color(model_id: str, rank: int) -> QColor:
    """Return the stable QColor for a model at the given descending-spend rank.

    rank 0 → the panel accent (heaviest spender / bottom floor band). Ranks past
    the ring length wrap, with the model-id hash deciding the wrapped slot so two
    models that wrap onto the same lap don't necessarily share a hue.
    """
    ring = _ring()
    n = len(ring)
    r = max(0, int(rank))
    if r < n:
        return QColor(ring[r])
    # Past the first lap: spread by a hash offset so wrapped models differ.
    return QColor(ring[(r + _hash_offset(model_id or "", n)) % n])
