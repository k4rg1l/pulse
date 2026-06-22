"""Pytest configuration for Pulse.

Two jobs:
1. Make the repo-root modules importable from tests/ (pytest's default
   prepend-import mode only adds the test file's own dir to sys.path; a
   root-level conftest.py makes pytest add the repo root too).
2. SAFETY: redirect APPDATA to a throwaway temp dir for every test so no
   test can ever read or clobber the user's real %APPDATA%/Pulse state
   (settings.json, state.json). `persistence.state_dir()` reads APPDATA
   fresh on each call, so patching the env var is sufficient and total.
"""
import sys
from pathlib import Path

import pytest

# Belt-and-suspenders: ensure repo root is importable even if pytest's
# rootdir detection changes.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture(autouse=True)
def isolate_appdata(tmp_path, monkeypatch):
    """Point APPDATA at a per-test temp dir so file I/O in persistence.py
    and settings.py never touches the user's real data."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path
