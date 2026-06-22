#!/usr/bin/env python3
"""Record that /security-review approved the EXACT current staged diff.

Run this AFTER `/security-review` and after staging any fixes the review asked
for. It writes `.git/pulse_secreview_ok` with the SHA-256 of the staged diff.
The PreToolUse gate (`tools/secreview_gate.py`) then lets `git commit` through
only while that hash still matches what's staged — so the approval is bound to
this exact change set and can't be reused for a different diff.

The marker lives inside `.git/`, so it is never committed and stays local.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time


def _run_git(args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def main() -> int:
    diff = _run_git(["diff", "--cached"]).stdout
    if not diff.strip():
        print(
            "[security-review] Nothing staged. Stage what you intend to commit "
            "(git add ...) before approving."
        )
        return 1

    h = hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()

    gitdir = (_run_git(["rev-parse", "--git-dir"]).stdout or "").strip() or ".git"
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(os.getcwd(), gitdir)
    marker = os.path.join(gitdir, "pulse_secreview_ok")

    stat = _run_git(["diff", "--cached", "--stat"]).stdout.strip()
    with open(marker, "w", encoding="utf-8") as f:
        json.dump(
            {
                "staged_diff_sha256": h,
                "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "diffstat": stat,
            },
            f,
            indent=2,
        )

    print(
        f"[security-review] Approved staged diff {h[:12]}...\n"
        f"git commit is unlocked for this exact diff. Any further `git add` "
        f"will require re-review.\n\n{stat}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
