"""Pure alert/severity helpers for Pulse (no Qt, no I/O — unit-tested).

Kept dependency-light and side-effect-free so it is trivially importable from
tests. ``main._openrouter_severity`` drives the OpenRouter status dot from this;
the tray's balance toasts (``tray_icon.py``) apply the same thresholds and could
share this helper in a future DRY pass.
"""


def balance_severity(remaining, warning, critical):
    """Map remaining credit ($) to a status severity, honoring the caller's
    configured thresholds (the user's ``balance_warning`` / ``balance_critical``
    settings). Returns ``"normal"`` | ``"warning"`` | ``"critical"``.

    - ``remaining is None`` (balance unknown) -> ``"normal"``: absence of data
      is not an alert.
    - Boundaries are inclusive: ``remaining == critical`` -> ``"critical"`` and
      ``remaining == warning`` -> ``"warning"``.
    """
    if remaining is None:
        return "normal"
    if remaining <= critical:
        return "critical"
    if remaining <= warning:
        return "warning"
    return "normal"
