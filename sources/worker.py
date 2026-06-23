"""Generic source-polling worker + trigger.

Mirrors the OpenRouter APIWorker/FetchTrigger pattern but for any Source:
the worker lives on a dedicated QThread, polls a source by id (all I/O off
the main thread), and emits the result back to the main thread where the
controller hands it to the source's card.render(). Source.poll() handles its
own errors, but we also guard here so one bad source can't take down the
thread.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

log = logging.getLogger("pulse.sources")


class SourceTrigger(QObject):
    """Main-thread -> worker-thread request to poll a source."""
    poll = Signal(str)  # source_id


class SourceWorker(QObject):
    """Worker-thread side: runs Source.poll() and marshals the result back."""
    polled = Signal(str, object)  # (source_id, data | None)

    def __init__(self, sources):
        super().__init__()
        self._sources = {s.source_id: s for s in sources}

    @Slot(str)
    def poll(self, source_id: str):
        src = self._sources.get(source_id)
        if src is None:
            return
        try:
            data = src.poll()
        except Exception:  # defensive: poll() shouldn't raise, but never crash the thread
            log.exception("source %r poll crashed", source_id)
            data = None
        self.polled.emit(source_id, data)
