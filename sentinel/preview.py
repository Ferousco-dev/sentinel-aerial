"""Interactive preview window — the Phase 1 self-test / operator view.

Renders the live feed with a lightweight telemetry HUD (source, FPS, resolution)
and an on-demand snapshot key. Later phases replace this OpenCV window with the
FastAPI dashboard, but the preview stays useful for quickly validating a feed.
"""

from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime

import cv2

from .config import CaptureConfig
from .logging_config import get_logger
from .video import FrameSource

_log = get_logger("sentinel.preview")

_WINDOW = "Sentinel · Phase 1 feed  [q]uit  [s]napshot"


class _FpsMeter:
    """Rolling FPS estimate over a sliding window of frame timestamps."""

    def __init__(self, window: int = 30) -> None:
        self._stamps: deque[float] = deque(maxlen=window)

    def tick(self) -> float:
        now = time.monotonic()
        self._stamps.append(now)
        if len(self._stamps) < 2:
            return 0.0
        span = self._stamps[-1] - self._stamps[0]
        return (len(self._stamps) - 1) / span if span > 0 else 0.0


def run_preview(source: FrameSource, capture_config: CaptureConfig) -> None:
    """Blocking preview loop. Returns when the operator presses ``q``."""
    os.makedirs(capture_config.snapshot_dir, exist_ok=True)
    cv2.namedWindow(_WINDOW, cv2.WINDOW_NORMAL)
    fps = _FpsMeter()

    _log.info("Preview started · source=%s", source.descriptor)
    _log.info("Keys: q=quit, s=snapshot")

    with source:
        while True:
            ok, frame = source.read()
            if not ok or frame is None:
                _log.warning("No frame; pausing before retry.")
                time.sleep(capture_config.read_retry_pause_s)
                continue

            rate = fps.tick()
            self_h, self_w = frame.shape[:2]
            hud = frame.copy()
            label = f"{source.kind.value}  {rate:4.1f} FPS  {self_w}x{self_h}"
            # Draw a dark backing bar so the text stays legible over any scene.
            cv2.rectangle(hud, (0, 0), (self_w, 34), (0, 0, 0), -1)
            cv2.putText(hud, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 255, 0), 2, cv2.LINE_AA)

            cv2.imshow(_WINDOW, hud)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                _save_snapshot(frame, capture_config.snapshot_dir)

    cv2.destroyAllWindows()
    _log.info("Preview stopped.")


def _save_snapshot(frame, directory: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(directory, f"snap_{stamp}.jpg")
    cv2.imwrite(path, frame)
    _log.info("Snapshot saved: %s", path)
