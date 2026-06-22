"""Unit tests for the GPU source. The NVML I/O is validated by live read
(see docs/TESTING.md); here we cover the pure GpuStats math, name trimming,
graceful hiding, and that the card renders in both states without error.
"""
from sources.gpu.nvml import GpuStats
from sources.gpu.source import GpuSource
from settings import Settings


def test_memory_properties():
    s = GpuStats(mem_used=3_000_000_000, mem_total=12_900_000_000)
    assert round(s.mem_used_gb, 1) == 3.0
    assert round(s.mem_total_gb, 1) == 12.9
    assert round(s.mem_percent) == 23


def test_mem_percent_handles_zero_total():
    assert GpuStats(mem_total=0).mem_percent == 0.0


def test_short_name_trims_vendor_prefix():
    assert GpuStats(name="NVIDIA GeForce RTX 4070 Ti").short_name == "RTX 4070 Ti"
    assert GpuStats(name="NVIDIA A100-SXM4").short_name == "A100-SXM4"
    assert GpuStats(name="Some Other GPU").short_name == "Some Other GPU"


def test_hidden_when_show_gpu_false_without_touching_nvml():
    # show_gpu False must short-circuit before any NVML call.
    assert GpuSource(Settings(show_gpu=False)).is_available() is False


def test_gpu_card_renders_both_states(qapp):
    from sources.gpu.card import GpuCard
    c = GpuCard()
    c.render(GpuStats(name="NVIDIA GeForce RTX 4070 Ti", util=42,
                      mem_used=6_000_000_000, mem_total=12_900_000_000, temp=63, power=210.0))
    c.resize(388, c.height())
    c.grab()  # forces paintEvent — must not raise
    assert c.height() > 0
    c.render(None)        # unavailable state
    c.resize(388, c.height())
    c.grab()
