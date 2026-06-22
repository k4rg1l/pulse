"""Widget-logic tests for the neutral source host (the agnostic migration):
OpenRouter and Claude mount as peer section-groups, ordered by source_order,
and the pinned-models card list still rebuilds correctly.

Uses the offscreen `qapp` fixture (no real display). These complement the
pure-logic tests; full UI behaviour is covered by docs/TESTING.md recipes.
"""
from PySide6.QtWidgets import QLabel

from dashboard import Dashboard
from persistence import History
from settings import Settings


def _group_order(dash):
    """The source_ids of the mounted group widgets, top to bottom."""
    ids = []
    for i in range(dash._source_host.count()):
        w = dash._source_host.itemAt(i).widget()
        sid = next((k for k, v in dash._source_widgets.items() if v is w), None)
        ids.append(sid)
    return ids


def test_openrouter_is_mounted_as_a_source_group(qapp):
    d = Dashboard(History(), Settings())
    assert "openrouter" in d._source_widgets
    assert _group_order(d) == ["openrouter"]


def test_default_order_places_claude_after_openrouter(qapp):
    d = Dashboard(History(), Settings())
    d.mount_source("claude", "Claude", QLabel("claude card"))  # as the controller does
    assert _group_order(d) == ["openrouter", "claude"]


def test_source_order_setting_reorders_peers(qapp):
    d = Dashboard(History(), Settings(source_order=["claude", "openrouter"]))
    d.mount_source("claude", "Claude", QLabel("claude card"))
    assert _group_order(d) == ["claude", "openrouter"]  # claude now on top


def test_unknown_source_falls_to_the_bottom(qapp):
    d = Dashboard(History(), Settings(source_order=["openrouter", "claude"]))
    d.mount_source("claude", "Claude", QLabel("c"))
    d.mount_source("gpu", "GPU", QLabel("g"))  # not in source_order
    assert _group_order(d) == ["openrouter", "claude", "gpu"]


def test_pin_unpin_rebuilds_cards(qapp):
    d = Dashboard(History(), Settings(tracked_models=["a/b"]))
    d.set_tracked_models(["a/b", "c/d"])
    assert set(d._pinned_cards) == {"a/b", "c/d"}
    d.set_tracked_models(["a/b"])
    assert list(d._pinned_cards) == ["a/b"]


def test_render_card_for_a_source(qapp):
    d = Dashboard(History(), Settings())

    class FakeCard(QLabel):
        def __init__(self):
            super().__init__()
            self.rendered = None

        def render(self, data):
            self.rendered = data

    card = FakeCard()
    d.mount_source("claude", "Claude", card)
    d.update_source("claude", {"x": 1})
    assert card.rendered == {"x": 1}
