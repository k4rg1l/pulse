"""Deterministic tests for widgets.GradientStrip — the rainbow top strip.

The strip paints one full hue wheel across its width, phase-shifted a step
per tick. Seamlessness is a math property, not a visual judgement: the
wheel's cycle count is an integer, so the first and last gradient stops
carry the SAME color at EVERY phase — that is what guarantees no visible
seam or restart as the wheel drifts. These tests measure that invariant
directly (the paint shares its stop list via GradientStrip._stops).
"""
from widgets import GradientStrip


def test_ends_meet_at_every_phase(qapp):
    """No-seam condition: the stop at position 0.0 and the stop at 1.0 must
    be the exact same color regardless of the animation phase."""
    strip = GradientStrip()
    for phase in (0.0, 0.1, 0.25, 0.5, 0.73, 0.996):
        strip._offset = phase
        stops = strip._stops()
        assert stops[0][0] == 0.0
        assert stops[-1][0] == 1.0
        assert stops[0][1].getRgb() == stops[-1][1].getRgb()


def test_stops_span_unit_range_monotonically(qapp):
    strip = GradientStrip()
    positions = [p for p, _ in strip._stops()]
    assert positions[0] == 0.0
    assert positions[-1] == 1.0
    assert positions == sorted(positions)
    assert len(positions) == GradientStrip.STOPS + 1


def test_adjacent_stops_step_one_wheel_segment(qapp):
    """Hue advances by exactly 1/STOPS between neighbours (mod the wheel):
    one full integer wheel across the width — the no-seam precondition."""
    strip = GradientStrip()
    strip._offset = 0.37
    stops = strip._stops()
    step = 1.0 / GradientStrip.STOPS
    for (_, c0), (_, c1) in zip(stops, stops[1:]):
        dh = (c1.hueF() - c0.hueF()) % 1.0
        # QColor quantizes hue to 1/100ths of a degree; allow that error.
        assert abs(dh - step) < 1e-3


def test_tick_advances_phase_by_speed_and_wraps(qapp):
    """The drift is a continuous phase shift: each tick moves the wheel by
    SPEED, and wrapping past 1.0 lands exactly where the cycle continues —
    never a jump."""
    strip = GradientStrip()
    strip.resize(560, 4)
    strip.show()  # _tick() no-ops while hidden
    strip._offset = 1.0 - GradientStrip.SPEED / 2
    before = strip._offset
    strip._tick()
    assert 0.0 <= strip._offset < 1.0
    delta = (strip._offset - before) % 1.0
    assert abs(delta - GradientStrip.SPEED) < 1e-9


def test_paint_smoke_renders_a_spectrum(qapp):
    """grab() drives a real offscreen paintEvent; the strip must render
    multiple distinct hue families across its width — a rainbow, not the
    old flat accent bar."""
    strip = GradientStrip()
    strip.resize(560, 4)
    img = strip.grab().toImage()
    # y=3 stays inside the rounded-corner clip for x >= 8 (radius 12).
    samples = [img.pixelColor(x, 3) for x in range(8, 552, 34)]
    hue_families = {c.hue() // 30 for c in samples if c.hue() >= 0}
    assert len(hue_families) >= 4
