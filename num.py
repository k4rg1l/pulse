"""Tiny numeric-coercion helpers shared across the data layer.

Upstream OpenRouter fields are frequently JSON strings ("0.0000012", "8685") or
missing; these coerce to a number with a safe default instead of raising. Pass
``default=None`` for a *nullable* result when "absent" must be told apart from a
real 0 (e.g. a ranking with no delta vs. a delta of 0).
"""


def as_float(v, default=0.0):
    """Coerce ``v`` to ``float``. ``None`` / ``""`` / non-numeric -> ``default``."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def as_int(v, default=0):
    """Coerce ``v`` to ``int`` (via ``float`` first, so "1.0" and 1.0 both work).
    ``None`` / ``""`` / non-numeric -> ``default``."""
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default
