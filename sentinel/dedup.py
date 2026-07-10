"""Phase 4 (T5) — per-class cooldown de-duplication.

The detector fires on every frame, so a stationary person would otherwise be
written to the event log dozens of times a second. :class:`CooldownFilter`
collapses that noise: once a class is logged, further detections of that class
are suppressed until ``cooldown_s`` has elapsed.

Semantics — **per-class, whole-frame trigger**:

* State is a single ``last-fired`` timestamp per class *name*. The key space is
  therefore bounded by the model's class vocabulary (≤ 80 for COCO), so the
  filter cannot leak memory across an arbitrarily long flight.
* When a class "fires" (first sighting, or its cooldown has elapsed), **every**
  detection of that class in the current frame is passed through — so a frame
  with three people logs all three, capturing the whole scene at the trigger
  moment — and the cooldown restarts.
* While a class is in cooldown, all of its detections that frame are suppressed.

This is deliberately identity-free: it does not track individual objects across
frames (that is tracking, out of scope here). Two people standing apart share
the ``person`` cooldown; distinguishing them would require a tracker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .detect import Detection
from .logging_config import get_logger

_log = get_logger("sentinel.dedup")


@dataclass
class DedupStats:
    """Running counters for observability (HUD / dashboard)."""

    seen: int = 0
    logged: int = 0
    suppressed: int = 0


class CooldownFilter:
    """Suppresses repeat detections of a class within a cooldown window."""

    def __init__(self, cooldown_s: float = 3.0) -> None:
        if cooldown_s < 0:
            raise ValueError("cooldown_s must be non-negative.")
        self._cooldown = cooldown_s
        self._last_fired: dict[str, float] = {}
        self.stats = DedupStats()

    def filter(self, detections: list[Detection],
               now: float | None = None) -> list[Detection]:
        """Return the subset of ``detections`` that should be logged.

        ``now`` is the frame timestamp; defaults to the wall clock. The decision
        per class is made from the state at frame start, then timestamps are
        updated — so multiple detections of a firing class in the same frame are
        all admitted together, not split by an intra-frame update.
        """
        if not detections:
            return []
        stamp = time.time() if now is None else now
        self.stats.seen += len(detections)

        # A cooldown of 0 disables suppression entirely (log everything).
        if self._cooldown == 0:
            self.stats.logged += len(detections)
            return list(detections)

        # Decide which classes fire this frame, using pre-update state.
        present = {d.cls_name for d in detections}
        fired = {
            name for name in present
            if stamp - self._last_fired.get(name, float("-inf")) >= self._cooldown
        }
        for name in fired:
            self._last_fired[name] = stamp

        allowed = [d for d in detections if d.cls_name in fired]
        self.stats.logged += len(allowed)
        self.stats.suppressed += len(detections) - len(allowed)
        return allowed

    def reset(self) -> None:
        """Clear cooldown state (e.g. at the start of a new session)."""
        self._last_fired.clear()

    @property
    def tracked_classes(self) -> int:
        """Number of classes currently holding cooldown state (bounded)."""
        return len(self._last_fired)
