"""Local Claude Code token accounting from ~/.claude/projects/**/*.jsonl.

`aggregate_tokens()` is pure and unit-tested. `aggregate_recent()` does the
file I/O on a worker thread and caches per-file aggregates by (mtime, size)
so unchanged transcripts (some are 10+ MB) aren't re-parsed every poll. No
network, no auth — purely local reads, and we never modify the files (they're
actively written by Claude Code; a partial last line is tolerated).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


def projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


@dataclass
class TokenStats:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    messages: int = 0
    web_searches: int = 0
    by_model: Dict[str, int] = field(default_factory=dict)  # model -> total tokens

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_creation

    @property
    def cache_efficiency(self) -> float:
        """Fraction of input tokens served from cache (0..1); higher is better."""
        denom = self.cache_read + self.input
        return (self.cache_read / denom) if denom else 0.0

    def merge(self, other: "TokenStats") -> None:
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.cache_creation += other.cache_creation
        self.messages += other.messages
        self.web_searches += other.web_searches
        for m, v in other.by_model.items():
            self.by_model[m] = self.by_model.get(m, 0) + v


def _epoch(ts) -> Optional[float]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def aggregate_tokens(records: Iterable[dict], since_ts: Optional[float] = None) -> TokenStats:
    """Pure: sum token usage over assistant messages.

    `records` are parsed JSONL line-objects. Only ``type == "assistant"``
    lines with a ``message.usage`` object count. If `since_ts` is given, only
    records whose ISO `timestamp` is >= since_ts are included.
    """
    stats = TokenStats()
    for obj in records:
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        if since_ts is not None:
            ts = _epoch(obj.get("timestamp"))
            if ts is not None and ts < since_ts:
                continue
        msg = obj.get("message") or {}
        usage = msg.get("usage") or {}
        if not usage:
            continue

        def _int(v):
            try:
                return int(v or 0)
            except (ValueError, TypeError):
                return 0

        i = _int(usage.get("input_tokens"))
        o = _int(usage.get("output_tokens"))
        cr = _int(usage.get("cache_read_input_tokens"))
        cc = _int(usage.get("cache_creation_input_tokens"))
        stats.input += i
        stats.output += o
        stats.cache_read += cr
        stats.cache_creation += cc
        stats.messages += 1
        stu = usage.get("server_tool_use") or {}
        stats.web_searches += _int(stu.get("web_search_requests"))
        model = msg.get("model") or "unknown"
        stats.by_model[model] = stats.by_model.get(model, 0) + (i + o + cr + cc)
    return stats


def iter_records(path: Path):
    """Yield parsed JSON objects from a JSONL file, skipping blank/malformed
    lines (the final line may be mid-write during an active session)."""
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def aggregate_recent(
    since_ts: float,
    root: Optional[Path] = None,
    cache: Optional[Dict[str, Tuple[Tuple[float, int], TokenStats]]] = None,
) -> TokenStats:
    """Aggregate tokens from session files modified since `since_ts`.

    A Claude session is a single sitting, so a file last modified within the
    window contains that window's activity; we aggregate whole recent files
    (and skip files untouched since `since_ts` entirely). `cache` maps
    str(path) -> ((mtime, size), TokenStats) so an unchanged file is reused
    instead of re-parsed. Mutates `cache` in place. Worker-thread only.
    """
    root = root or projects_dir()
    total = TokenStats()
    if not root.exists():
        return total
    for path in root.rglob("*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime < since_ts:
            continue  # nothing written in-window -> no in-window records
        sig = (st.st_mtime, st.st_size)
        file_stats: Optional[TokenStats] = None
        if cache is not None:
            cached = cache.get(str(path))
            if cached is not None and cached[0] == sig:
                file_stats = cached[1]
        if file_stats is None:
            file_stats = aggregate_tokens(iter_records(path))
            if cache is not None:
                cache[str(path)] = (sig, file_stats)
        total.merge(file_stats)
    return total
