"""Phase 4 — SQLite event logging (T4).

Persists detections to a local SQLite database so the pipeline produces an
auditable record that the dashboard (Phase 5) can tail and the post-flight
report (Phase 7) can summarise.

Scope of this issue (T4): schema + writer only. Per-class de-duplication
cooldown is a separate concern handled in T5 (``feature/log-dedup``); this
writer records every detection it is given.

Design:

* **Idempotent schema.** ``CREATE TABLE/INDEX IF NOT EXISTS`` — safe to open the
  same DB across many runs.
* **Sessions.** Every row carries a ``session_id`` (one per :class:`EventLog`
  instance) so a single DB can hold multiple flights and the report can compute
  per-session duration and counts.
* **Concurrency-safe.** Opened with ``check_same_thread=False`` and guarded by a
  lock so the Phase 5 dashboard can read/write from its own thread. WAL mode
  keeps writers from blocking readers.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass

from .config import LogConfig
from .detect import Detection
from .logging_config import get_logger

_log = get_logger("sentinel.eventlog")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS detections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    ts          REAL    NOT NULL,
    cls_id      INTEGER NOT NULL,
    cls_name    TEXT    NOT NULL,
    confidence  REAL    NOT NULL,
    x1          INTEGER NOT NULL,
    y1          INTEGER NOT NULL,
    x2          INTEGER NOT NULL,
    y2          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_detections_ts      ON detections(ts);
CREATE INDEX IF NOT EXISTS idx_detections_session ON detections(session_id);
CREATE INDEX IF NOT EXISTS idx_detections_class   ON detections(cls_name);
"""


@dataclass(frozen=True)
class SessionSummary:
    """Aggregate stats for one logged session (used by the Phase 7 report)."""

    session_id: str
    total: int
    first_ts: float | None
    last_ts: float | None
    counts_by_class: dict[str, int]

    @property
    def duration_s(self) -> float:
        if self.first_ts is None or self.last_ts is None:
            return 0.0
        return max(0.0, self.last_ts - self.first_ts)


def _default_session_id() -> str:
    """Human-sortable session id from the wall clock."""
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


class EventLog:
    """Thread-safe SQLite writer for detection events.

    Use as a context manager so the connection is always closed::

        with EventLog(LogConfig()) as log:
            log.write_many(detections)
    """

    def __init__(self, config: LogConfig | None = None,
                 session_id: str | None = None) -> None:
        self._cfg = config or LogConfig()
        self.session_id = session_id or _default_session_id()
        self._lock = threading.Lock()

        self._conn = sqlite3.connect(
            self._cfg.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self._cfg.use_wal:
            # WAL: concurrent readers (dashboard) don't block the writer.
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        _log.info("EventLog open: db=%s session=%s",
                  self._cfg.db_path, self.session_id)

    # -- writes -------------------------------------------------------------
    def write(self, detection: Detection, ts: float | None = None) -> int:
        """Insert one detection; returns its row id."""
        return self.write_many([detection], ts)

    def write_many(self, detections: list[Detection],
                   ts: float | None = None) -> int:
        """Insert a batch of detections in one transaction; returns the count.

        A single timestamp is shared across the batch so all detections from the
        same frame are grouped in time (the caller passes the frame's capture
        time, or ``None`` to stamp now).
        """
        if not detections:
            return 0
        stamp = time.time() if ts is None else ts
        rows = [
            (self.session_id, stamp, d.cls_id, d.cls_name, d.confidence,
             *d.bbox)
            for d in detections
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO detections "
                "(session_id, ts, cls_id, cls_name, confidence, x1, y1, x2, y2) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    # -- reads (used by dashboard & report) ---------------------------------
    def count(self, session_only: bool = True) -> int:
        """Number of logged detections (this session by default)."""
        with self._lock:
            if session_only:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM detections WHERE session_id = ?",
                    (self.session_id,))
            else:
                cur = self._conn.execute("SELECT COUNT(*) FROM detections")
            return int(cur.fetchone()[0])

    def counts_by_class(self, session_only: bool = True) -> dict[str, int]:
        """Detection counts grouped by class name."""
        query = ("SELECT cls_name, COUNT(*) AS n FROM detections "
                 + ("WHERE session_id = ? " if session_only else "")
                 + "GROUP BY cls_name ORDER BY n DESC")
        args = (self.session_id,) if session_only else ()
        with self._lock:
            cur = self._conn.execute(query, args)
            return {row["cls_name"]: int(row["n"]) for row in cur.fetchall()}

    def summary(self) -> SessionSummary:
        """Aggregate summary for the current session."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
                "FROM detections WHERE session_id = ?", (self.session_id,))
            row = cur.fetchone()
        return SessionSummary(
            session_id=self.session_id,
            total=int(row["n"]),
            first_ts=row["first_ts"],
            last_ts=row["last_ts"],
            counts_by_class=self.counts_by_class(session_only=True),
        )

    # -- lifecycle ----------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]
        _log.info("EventLog closed: session=%s", self.session_id)

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
