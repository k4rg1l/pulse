"""Tests for the mandatory /security-review commit gate
(tools/secreview_gate.py + tools/secreview_approve.py).

These run the real scripts as subprocesses so we validate the exact exit codes
the PreToolUse hook depends on: 0 = allow the tool call, 2 = block it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tools" / "secreview_gate.py"
APPROVE = ROOT / "tools" / "secreview_approve.py"


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _make_repo(tmp_path, content="print('hi')\n"):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text(content, encoding="utf-8")
    _git(repo, "add", "app.py")
    return repo


def _run_gate(command, cwd):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _approve(repo):
    return subprocess.run(
        [sys.executable, str(APPROVE)], cwd=repo, capture_output=True, text=True
    )


def test_blocks_commit_without_marker(tmp_path):
    repo = _make_repo(tmp_path)
    r = _run_gate("git commit -m 'x'", repo)
    assert r.returncode == 2
    assert "security-review" in r.stderr.lower()


def test_allows_commit_after_approval(tmp_path):
    repo = _make_repo(tmp_path)
    assert _approve(repo).returncode == 0
    r = _run_gate("git commit -m 'x'", repo)
    assert r.returncode == 0, r.stderr


def test_blocks_when_staged_diff_changes_after_approval(tmp_path):
    repo = _make_repo(tmp_path)
    assert _approve(repo).returncode == 0
    # Change what's staged AFTER approving — the approval must no longer apply.
    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    r = _run_gate("git commit -m 'x'", repo)
    assert r.returncode == 2


def test_blocks_commit_all_flag_even_with_marker(tmp_path):
    repo = _make_repo(tmp_path)
    assert _approve(repo).returncode == 0
    r = _run_gate("git commit -am 'x'", repo)
    assert r.returncode == 2
    assert "-a" in r.stderr.lower() or "all" in r.stderr.lower()


def test_dash_a_inside_message_is_not_a_false_positive(tmp_path):
    repo = _make_repo(tmp_path)
    assert _approve(repo).returncode == 0
    # "-a" appears only inside the commit message, not as a flag.
    r = _run_gate('git commit -m "fix -a flag handling"', repo)
    assert r.returncode == 0, r.stderr


def test_message_with_semicolon_and_dash_a_is_not_all_flag(tmp_path):
    # A message containing BOTH ';' and '-a' must not be misread: quotes are
    # stripped before splitting, so the ';' inside the message can't break the
    # quoted span and leak the '-a'. (Regression: this exact case blocked a
    # real commit.)
    repo = _make_repo(tmp_path)
    assert _approve(repo).returncode == 0
    r = _run_gate("git commit -m 'fix; refuses git commit -a now'", repo)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    "cmd",
    [
        "git status",
        "git log --grep commit",
        "echo git commit",
        "ls -la",
    ],
)
def test_allows_non_commit_commands(tmp_path, cmd):
    repo = _make_repo(tmp_path)
    r = _run_gate(cmd, repo)
    assert r.returncode == 0, (cmd, r.stderr)


def test_allows_when_nothing_staged(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    r = _run_gate("git commit -m 'x'", repo)
    # Nothing to review; let git itself report "nothing to commit".
    assert r.returncode == 0


def test_chained_commit_is_gated(tmp_path):
    repo = _make_repo(tmp_path)
    r = _run_gate("echo hi && git commit -m 'x'", repo)
    assert r.returncode == 2
