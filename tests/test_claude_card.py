"""Paint smoke test for ClaudeCard — parity with test_gpu/test_system, and
regression coverage for the BaseCard fold (the card's paint path was previously
untested). Forces paintEvent in both the populated and unavailable states.
"""
from datetime import datetime, timedelta, timezone

from sources.claude.source import ClaudeCardData
from sources.claude.usage import ClaudeUsage, UsageWindow
from sources.claude.jsonl import TokenStats


def _populated() -> ClaudeCardData:
    now = datetime.now(timezone.utc)
    usage = ClaudeUsage(windows=[
        UsageWindow(key="session", label="5h session", utilization=38.0,
                    resets_at=now + timedelta(hours=2, minutes=36), severity="normal"),
        UsageWindow(key="weekly_all", label="7d all", utilization=40.0,
                    resets_at=now + timedelta(hours=11), severity="warning"),
        UsageWindow(key="weekly_opus", label="7d Sonnet", utilization=2.0,
                    resets_at=now + timedelta(hours=11), severity="critical"),
    ])
    tokens = TokenStats(input=100, output=50, cache_read=900, messages=6937,
                        by_model={"claude-opus-4-8": 690, "claude-opus-4-7": 300})
    return ClaudeCardData(subscription="max", usage=usage, tokens=tokens,
                          usage_status="live", usage_age_seconds=0.0)


def test_claude_card_renders_both_states(qapp):
    from sources.claude.card import ClaudeCard
    c = ClaudeCard()
    c.render(_populated())          # windows (normal/warning/critical) + token footer
    c.resize(388, c.height())
    c.grab()                        # forces paintEvent — must not raise
    assert c.height() > 0
    c.render(None)                  # unavailable state (header + message, no footer)
    c.resize(388, c.height())
    c.grab()
    assert c.height() > 0
