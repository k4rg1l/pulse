"""Tests for each source's rail-status severity() mapping (UI overhaul).

Called unbound (self=None) since severity() only reads its `data` argument —
this avoids constructing the NVML/psutil readers in a unit test.
"""
from types import SimpleNamespace

from sources.gpu.source import GpuSource
from sources.system.source import SystemSource
from sources.claude.source import ClaudeSource, ClaudeCardData
from sources.claude.usage import ClaudeUsage, UsageWindow


def _win(sev):
    return UsageWindow(key="x", label="x", utilization=10.0, severity=sev)


def test_gpu_severity_by_temp():
    sev = GpuSource.severity
    assert sev(None, None) == "normal"
    assert sev(None, SimpleNamespace(temp=50)) == "normal"
    assert sev(None, SimpleNamespace(temp=72)) == "warning"
    assert sev(None, SimpleNamespace(temp=85)) == "critical"
    assert sev(None, SimpleNamespace(temp=None)) == "normal"


def test_system_severity_by_load():
    sev = SystemSource.severity
    assert sev(None, None) == "normal"
    assert sev(None, SimpleNamespace(cpu=10, ram_percent=40)) == "normal"
    assert sev(None, SimpleNamespace(cpu=88, ram_percent=40)) == "warning"
    assert sev(None, SimpleNamespace(cpu=10, ram_percent=96)) == "critical"


def test_claude_severity_from_windows_and_status():
    sev = ClaudeSource.severity
    assert sev(None, None) == "normal"
    assert sev(None, ClaudeCardData(usage_status="expired")) == "warning"

    normal = ClaudeUsage(windows=[_win("normal"), _win("normal")])
    assert sev(None, ClaudeCardData(usage_status="live", usage=normal)) == "normal"

    warn = ClaudeUsage(windows=[_win("normal"), _win("warning")])
    assert sev(None, ClaudeCardData(usage_status="live", usage=warn)) == "warning"

    crit = ClaudeUsage(windows=[_win("warning"), _win("critical")])
    assert sev(None, ClaudeCardData(usage_status="live", usage=crit)) == "critical"
