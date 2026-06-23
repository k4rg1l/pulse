#!/usr/bin/env python3
"""PreToolUse gate: block `git commit` unless /security-review approved the
*exact* staged diff.

Wired from `.claude/settings.json` as a PreToolUse hook on the Bash tool. It
reads the hook payload JSON on stdin, looks at the bash command, and:

  * allows anything that isn't a `git commit`            (exit 0),
  * blocks `git commit -a/--all` (stage explicitly so the review is honest),
  * blocks `git commit` unless `.git/pulse_secreview_ok` records the SHA-256
    of the currently-staged diff                          (exit 2).

The marker is written by `tools/secreview_approve.py` after a clean review.
Because it stores the staged-diff hash, ANY later change to what's staged
invalidates the approval — you can't review once and commit forever.

Design rule: **fail closed.** Any uncertainty (can't read git, can't parse the
payload) blocks the commit rather than letting it through.

Exit codes (PreToolUse contract): 0 = allow, 2 = block (stderr shown to the
agent). We only ever use those two.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

ALLOW = 0
BLOCK = 2

# A git-commit invocation as the subcommand of a segment, after optional
# global flags (-C dir, -c k=v, --opt). Anchored to the start of a segment so
# `git log --grep commit` and `echo "git commit"` do NOT match.
_COMMIT_RE = re.compile(
    r"^\s*git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+(?:=\S+)?\s+|-\w\s+)*commit\b"
)
_ALL_FLAG_RE = re.compile(r"(?:\s-\w*a\w*|\s--all)\b")
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def _block(msg: str) -> "None":
    sys.stderr.write(msg)
    sys.exit(BLOCK)


def _allow() -> "None":
    sys.exit(ALLOW)


def _read_payload() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        # Can't parse the hook payload. Don't guess — but also don't block
        # every unrelated Bash call. We can't know the command, so allow:
        # the worst case is a non-commit command proceeding (safe). A commit
        # can't reach the protected path without a parseable command anyway.
        return {}


def _strip_quoted(s: str) -> str:
    """Remove quoted spans (commit messages) so their contents — a ';', a
    '-a', even the words 'git commit' — can't cause a false split or a false
    `-a/--all` match."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", "", s)


def _commit_segments(cmd: str):
    """Segments that are real `git commit` invocations. Quotes are stripped
    FIRST so a commit message can't trip segmentation or detection."""
    segments = re.split(r"&&|\|\||;|\n", _strip_quoted(cmd))
    return [s for s in segments if _COMMIT_RE.search(s)]


def _run_git(args, cwd):
    # Force UTF-8: a diff with emoji/symbols would otherwise crash the default
    # Windows cp1252 decode and (fail-closed) block the commit. Must match
    # secreview_approve.py so the staged-diff hashes agree.
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace"
    )


def _staged_diff_hash(cwd: str) -> str:
    out = _run_git(["diff", "--cached"], cwd)
    return hashlib.sha256(out.stdout.encode("utf-8", "replace")).hexdigest()


def _marker_path(cwd: str) -> str:
    r = _run_git(["rev-parse", "--git-dir"], cwd)
    gitdir = (r.stdout or "").strip() or ".git"
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(cwd, gitdir)
    return os.path.join(gitdir, "pulse_secreview_ok")


_GUIDE = (
    "\n[security-review gate] Commit blocked - mandatory review not recorded "
    "for this diff.\n\n"
    "Before committing you MUST review the pending changes and approve the "
    "exact staged diff:\n"
    "  1. git add -A                       # stage exactly what you'll commit\n"
    "  2. /security-review                 # resolve findings; re-stage fixes\n"
    "  3. python tools/secreview_approve.py  # records the approved diff\n"
    "  4. re-run your git commit\n\n"
    "This gate is mandatory (see AGENTS.md). It fails closed by design: if you "
    "change what's staged after approving, you must review + approve again.\n"
)


def main() -> "None":
    payload = _read_payload()
    if payload.get("tool_name") not in (None, "Bash"):
        _allow()
    cmd = (payload.get("tool_input") or {}).get("command", "") or ""
    if not cmd:
        _allow()

    commit_segs = _commit_segments(cmd)
    if not commit_segs:
        _allow()

    cwd = payload.get("cwd") or os.getcwd()

    # Require explicit staging so /security-review + the approval cover exactly
    # what gets committed (otherwise `-a` sneaks in un-reviewed tracked edits).
    # commit_segs are already quote-stripped, so a `-a` inside a commit MESSAGE
    # is not a false positive.
    if any(_ALL_FLAG_RE.search(s) for s in commit_segs):
        _block(
            "\n[security-review gate] Refusing `git commit -a/--all`.\n"
            "Stage changes explicitly (git add ...) so the security review and "
            "approval cover exactly what will be committed, then approve with "
            "`python tools/secreview_approve.py`.\n"
        )

    try:
        cur = _staged_diff_hash(cwd)
    except Exception as e:  # fail closed
        _block(
            f"\n[security-review gate] Could not compute the staged diff "
            f"({e!r}); failing closed. Stage your changes and retry.\n"
        )

    if cur == _EMPTY_SHA:
        # Nothing staged — nothing to review; let git report 'nothing to
        # commit' (or handle an --amend message-only edit) itself.
        _allow()

    approved = None
    try:
        marker = _marker_path(cwd)
        if os.path.exists(marker):
            with open(marker, "r", encoding="utf-8") as f:
                approved = json.load(f).get("staged_diff_sha256")
    except Exception:
        approved = None

    if approved == cur:
        _allow()
    _block(_GUIDE)


if __name__ == "__main__":
    main()
