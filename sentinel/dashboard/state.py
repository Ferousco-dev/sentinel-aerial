"""Thread-safe hand-off between the pipeline thread and WebSocket clients.

Holds the latest annotated frame (already JPEG-encoded), a bounded ring of recent
events (each with a monotonically increasing id so a client can ask for
"everything since id N"), and the current stats. All access is guarded by a lock;
readers take cheap snapshots so they never block the producer for long.
"""

from __future__ import annotations

import threading
from collections import deque


class LiveState:
    """Shared, thread-safe snapshot of the running pipeline."""

    def __init__(self, max_events: int = 500) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._frame_seq = 0
        self._events: deque[dict] = deque(maxlen=max_events)
        self._event_seq = 0
        self._stats: dict = {}
        self._running = True

    # -- producer side (pipeline thread) ------------------------------------
    def publish_frame(self, jpeg: bytes, stats: dict) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._frame_seq += 1
            self._stats = dict(stats)

    def publish_events(self, events: list[dict]) -> None:
        """Append events, tagging each with a unique increasing id."""
        if not events:
            return
        with self._lock:
            for e in events:
                self._event_seq += 1
                self._events.append({"id": self._event_seq, **e})

    def set_running(self, running: bool) -> None:
        with self._lock:
            self._running = running

    # -- consumer side (WebSocket handlers) ---------------------------------
    def frame_snapshot(self) -> tuple[int, bytes | None, dict]:
        with self._lock:
            return self._frame_seq, self._jpeg, dict(self._stats)

    def events_since(self, last_id: int) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["id"] > last_id]

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def latest_event_id(self) -> int:
        with self._lock:
            return self._event_seq
