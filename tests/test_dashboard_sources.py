"""Widget-logic tests for the nav-rail command center (UI overhaul):
OpenRouter and the peer sources register as tabs, the rail orders them by
source_order, and the pinned-models card list still rebuilds correctly.

Uses the offscreen `qapp` fixture (no real display). These complement the
pure-logic tests; full UI behaviour is covered by docs/TESTING.md recipes.
"""
from PySide6.QtWidgets import QLabel

from dashboard import Dashboard
from persistence import History
from settings import Settings


def _rail_order(dash):
    """The source_ids on the nav-rail, top to bottom."""
    return [t["id"] for t in dash.nav_rail._tabs]


def test_openrouter_is_registered_as_a_tab(qapp):
    d = Dashboard(History(), Settings())
    assert "openrouter" in d._panels
    assert _rail_order(d) == ["openrouter"]


def test_default_order_places_claude_after_openrouter(qapp):
    d = Dashboard(History(), Settings())
    d.register_source_tab("claude", "Claude", "#D97757", QLabel("claude card"))
    assert _rail_order(d) == ["openrouter", "claude"]


def test_source_order_setting_reorders_peers(qapp):
    d = Dashboard(History(), Settings(source_order=["claude", "openrouter"]))
    d.register_source_tab("claude", "Claude", "#D97757", QLabel("claude card"))
    assert _rail_order(d) == ["claude", "openrouter"]  # claude now on top


def test_unknown_source_falls_to_the_bottom(qapp):
    d = Dashboard(History(), Settings(source_order=["openrouter", "claude"]))
    d.register_source_tab("claude", "Claude", "#D97757", QLabel("c"))
    d.register_source_tab("gpu", "GPU", "#76B900", QLabel("g"))  # not in order
    assert _rail_order(d) == ["openrouter", "claude", "gpu"]


def test_active_source_switches_the_stack(qapp):
    d = Dashboard(History(), Settings())
    d.register_source_tab("claude", "Claude", "#D97757", QLabel("claude card"))
    d.set_active_source("claude", animate=False)
    assert d._active_id == "claude"
    assert d._stack.currentWidget() is d._panels["claude"]


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
    d.register_source_tab("claude", "Claude", "#D97757", card)
    d.update_source("claude", {"x": 1})
    assert card.rendered == {"x": 1}
