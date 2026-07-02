"""Regression tests for the "Close when you click away" toggle.

Caught live 2026-07-01: with the dashboard open and the poller armed,
toggling the setting OFF neither stopped the outside-click timer nor cleared
the stale foreground reference — every cleanup/arm site was gated on the
CURRENT flag value. The orphaned timer then hid the dashboard within one
150ms tick of every subsequent open ("opens for a millisecond and closes
immediately"). These tests drive the state machine directly — no waits, no
real clicks — and lock the poller's lifecycle to visibility + the live
setting value.
"""
from dashboard import Dashboard
from persistence import History
from settings import Settings


def test_toggle_off_stops_a_running_poller(qapp):
    d = Dashboard(History(), Settings())
    d.show()
    d._outside_click_timer.start(150)   # armed, as after a real open
    d._show_foreground = 12345          # stale foreground ref
    d._on_set_dismiss(False)
    assert not d._outside_click_timer.isActive()
    assert d._show_foreground is None
    d.hide()


def test_reopen_after_toggle_off_leaves_poller_dead(qapp):
    """THE reported sequence: dismiss ON + open (armed) -> toggle OFF ->
    quick close -> reopen. Before the fix the old timer survived with a
    stale foreground ref and hid the dashboard on its first tick."""
    d = Dashboard(History(), Settings())
    d.toggle()                          # open
    d._outside_click_timer.start(150)   # poller armed (grace elapsed)
    d._show_foreground = 12345
    d._on_set_dismiss(False)            # user flips the setting off
    d.toggle()                          # quick close
    d.toggle()                          # reopen
    assert d.isVisible()
    assert not d._outside_click_timer.isActive()
    assert d._show_foreground is None
    d.hide()


def test_every_hide_path_stops_the_poller(qapp):
    """hide() is also reached via the ✕ button and the poller itself —
    hideEvent must stop the poller unconditionally, not just tray-toggle."""
    d = Dashboard(History(), Settings())
    d.show()
    d._outside_click_timer.start(150)
    d._show_foreground = 12345
    d.hide()
    assert not d._outside_click_timer.isActive()
    assert d._show_foreground is None


def test_toggle_on_while_open_arms_the_poller(qapp):
    """Flipping the setting ON with the dashboard open must take effect
    immediately (after the grace delay), not on the next open."""
    d = Dashboard(History(), Settings(dismiss_on_focus_loss=False))
    d.show()
    assert not d._outside_click_timer.isActive()
    d._on_set_dismiss(True)
    d._start_outside_click_poll()       # the grace singleShot's slot
    assert d._outside_click_timer.isActive()
    d.hide()
    assert not d._outside_click_timer.isActive()


def test_grace_fire_respects_late_disable_and_hide(qapp):
    """The delayed start must re-check state at fire time: a toggle-off or a
    close inside the 250ms grace window must not start the poller."""
    d = Dashboard(History(), Settings())
    d.show()
    d._on_set_dismiss(True)
    d._on_set_dismiss(False)            # flipped off within the grace window
    d._start_outside_click_poll()
    assert not d._outside_click_timer.isActive()

    d._on_set_dismiss(True)
    d.hide()                            # closed within the grace window
    d._start_outside_click_poll()
    assert not d._outside_click_timer.isActive()
