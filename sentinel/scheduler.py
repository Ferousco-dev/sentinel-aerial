"""Phase 3 (T3) — inference throttling / frame-skip.

YOLOv8n on a laptop CPU costs ~100-300 ms per frame, so detecting on *every*
captured frame drags the whole display loop down to a few FPS. Detections don't
actually change that fast, though — so :class:`DetectionScheduler` runs inference
only every so often and **reuses the last result** to annotate the frames in
between. Capture + draw is cheap, so the display stays smooth while detections
refresh several times a second.

Two things keep latency bounded:

* **No frame queue.** Each call processes the frame it is handed *now* and either
  runs inference or reuses the cache — nothing buffers, so latency can't
  accumulate under load (we always work on the freshest frame).
* **Time-based gate by default.** The interval is wall-clock, not frame-count, so
  the inference rate is stable regardless of how fast frames arrive.

The scheduler wraps a :class:`~sentinel.detect.Detector` and preserves its
interface shape, so callers annotate/log exactly as before.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .detect import Detection, Detector
from .logging_config import get_logger

_log = get_logger("sentinel.scheduler")

Frame = np.ndarray


@dataclass
class ScheduleStats:
    """Counters for observability (HUD / dashboard)."""

    frames: int = 0
    inferences: int = 0
    reuses: int = 0

    @property
    def infer_ratio(self) -> float:
        return self.inferences / self.frames if self.frames else 0.0


class DetectionScheduler:
    """Throttles a :class:`Detector`, reusing cached detections between runs."""

    def __init__(self, detector: Detector,
                 min_interval_s: float = 0.15,
                 every_n: int = 1) -> None:
        if min_interval_s < 0:
            raise ValueError("min_interval_s must be non-negative.")
        if every_n < 1:
            raise ValueError("every_n must be >= 1.")
        self._detector = detector
        self._min_interval_s = min_interval_s
        self._every_n = every_n
        self._last_run: float | None = None
        self._frame_idx = 0
        self._cached: list[Detection] = []
        self.stats = ScheduleStats()

    def _is_due(self, now: float) -> bool:
        if self._last_run is None:
            return True  # always detect on the first frame
        if self._min_interval_s > 0:
            return (now - self._last_run) >= self._min_interval_s
        return (self._frame_idx % self._every_n) == 0

    def process(self, frame: Frame,
                now: float | None = None) -> tuple[Frame, list[Detection], bool]:
        """Annotate ``frame``, running inference only when due.

        Returns ``(annotated_frame, detections, ran_inference)``. ``detections``
        is the fresh set when inference ran, else the reused cache. ``ran`` lets
        the caller log only genuinely new detections (not reused ones).
        """
        stamp = time.time() if now is None else now
        self._frame_idx += 1
        self.stats.frames += 1

        ran = self._is_due(stamp)
        if ran:
            self._cached = self._detector.detect(frame)
            self._last_run = stamp
            self.stats.inferences += 1
        else:
            self.stats.reuses += 1

        annotated = self._detector.annotate(frame, self._cached)
        return annotated, self._cached, ran

    @property
    def last_detections(self) -> list[Detection]:
        return list(self._cached)
