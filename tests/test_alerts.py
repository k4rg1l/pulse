"""Deterministic tests for the pure balance-severity helper.

Locks the P0.4 fix: the OpenRouter status severity must honor the USER's
configured balance thresholds, not hardcoded constants.
"""
from alerts import balance_severity


def test_unknown_balance_is_normal():
    assert balance_severity(None, 5.0, 1.0) == "normal"


def test_honors_user_thresholds_not_hardcoded_defaults():
    # Regression: the buggy code compared against a hardcoded 5.0 warning, so a
    # user who lowered their warning threshold to 3.0 still got warned at $4.
    # With the fix the caller's thresholds are honored: $4.00 remaining with a
    # 3.0 warning is NORMAL.
    assert balance_severity(4.0, warning=3.0, critical=1.0) == "normal"
    # ...and $2.50 crosses the user's 3.0 warning.
    assert balance_severity(2.5, warning=3.0, critical=1.0) == "warning"


def test_levels_and_inclusive_boundaries():
    assert balance_severity(10.0, 5.0, 1.0) == "normal"
    assert balance_severity(5.0, 5.0, 1.0) == "warning"    # == warning -> warning
    assert balance_severity(3.0, 5.0, 1.0) == "warning"
    assert balance_severity(1.0, 5.0, 1.0) == "critical"   # == critical -> critical
    assert balance_severity(0.0, 5.0, 1.0) == "critical"
