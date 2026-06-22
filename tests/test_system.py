"""Unit tests for the system vitals source. psutil I/O is validated by live
read (see docs/TESTING.md); here we cover the pure RAM math and the network
rate derivation (the bit most likely to regress).
"""
from sources.system.source import SystemSource
from sources.system.vitals import SystemStats, net_rate
from settings import Settings


def test_ram_properties():
    s = SystemStats(ram_used=21_000_000_000, ram_total=34_100_000_000)
    assert round(s.ram_used_gb, 1) == 21.0
    assert round(s.ram_total_gb, 1) == 34.1
    assert round(s.ram_percent) == 62


def test_ram_percent_handles_zero_total():
    assert SystemStats(ram_total=0).ram_percent == 0.0


def test_net_rate_returns_zero_without_baseline():
    assert net_rate(None, 100.0, 1000, 2000) == (0.0, 0.0)


def test_net_rate_bytes_per_second():
    prev = (100.0, 1000, 2000)
    up, down = net_rate(prev, 102.0, 1000 + 4000, 2000 + 8000)  # 2s window
    assert up == 2000.0 and down == 4000.0


def test_net_rate_clamps_counter_reset():
    prev = (100.0, 5000, 9000)
    assert net_rate(prev, 101.0, 1000, 1000) == (0.0, 0.0)  # adapter reset -> clamp


def test_net_rate_zero_dt():
    assert net_rate((100.0, 0, 0), 100.0, 500, 500) == (0.0, 0.0)


def test_hidden_when_show_system_false():
    assert SystemSource(Settings(show_system=False)).is_available() is False


def test_system_card_renders_both_states(qapp):
    from sources.system.card import SystemCard
    c = SystemCard()
    c.render(SystemStats(cpu=10.7, ram_used=21_000_000_000, ram_total=34_100_000_000,
                         net_up=312_000, net_down=4_200_000))
    c.resize(388, c.height())
    c.grab()
    assert c.height() > 0
    c.render(None)
    c.resize(388, c.height())
    c.grab()
