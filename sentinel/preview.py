"""Interactive preview window — the Phase 1/2 self-test / operator view.

Renders the live feed with a lightweight telemetry HUD (source, FPS, resolution,
and — once enhancement is active — the current adaptive quality tier and stage
latency). Supports a live enhancement toggle and a split-screen raw/enhanced
comparison, which is the quickest way to show the Phase 2 "before/after" on
stage. Later phases replace this OpenCV window with the FastAPI dashboard, but
the preview stays useful for quickly validating a feed.
"""

from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime

import cv2
import numpy as np

from .config import CaptureConfig, EnhanceConfig
from .enhance import FrameEnhancer
from .logging_config import get_logger
from .video import FrameSource

_log = get_logger("sentinel.preview")

_WINDOW = "Sentinel · feed  [q]uit [s]nap [e]nhance [c]ompare"


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


def run_preview(
    source: FrameSource,
    capture_config: CaptureConfig,
    enhance_config: EnhanceConfig | None = None,
    enhance_enabled: bool = False,
) -> None:
    """Blocking preview loop. Returns when the operator presses ``q``.

    Keys: ``q`` quit · ``s`` snapshot · ``e`` toggle enhancement ·
    ``c`` toggle raw/enhanced split view.
    """
    os.makedirs(capture_config.snapshot_dir, exist_ok=True)
    cv2.namedWindow(_WINDOW, cv2.WINDOW_NORMAL)
    fps = _FpsMeter()

    enhancer = FrameEnhancer(enhance_config or EnhanceConfig())
    do_enhance = enhance_enabled
    do_compare = False

    _log.info("Preview started · source=%s", source.descriptor)
    _log.info("Keys: q=quit, s=snapshot, e=enhance, c=compare")

    with source:
        while True:
            ok, raw = source.read()
            if not ok or raw is None:
                _log.warning("No frame; pausing before retry.")
                time.sleep(capture_config.read_retry_pause_s)
                continue

            processed = enhancer.process(raw) if do_enhance else raw
            rate = fps.tick()

            if do_compare and do_enhance:
                display = _side_by_side(raw, processed)
            else:
                display = processed.copy()

            _draw_hud(display, source.kind.value, rate, raw.shape,
                      do_enhance, enhancer, do_compare)

            cv2.imshow(_WINDOW, display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                _save_snapshot(processed, capture_config.snapshot_dir)
            elif key == ord("e"):
                do_enhance = not do_enhance
                _log.info("Enhancement %s", "ON" if do_enhance else "OFF")
            elif key == ord("c"):
                do_compare = not do_compare
                _log.info("Compare view %s", "ON" if do_compare else "OFF")

    cv2.destroyAllWindows()
    _log.info("Preview stopped.")


def _side_by_side(raw, processed):
    """Stack raw|enhanced horizontally with a dividing line and captions."""
    combo = np.hstack((raw, processed))
    mid = raw.shape[1]
    cv2.line(combo, (mid, 0), (mid, combo.shape[0]), (0, 0, 0), 2)
    cv2.putText(combo, "RAW", (10, combo.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2, cv2.LINE_AA)
    cv2.putText(combo, "ENHANCED", (mid + 10, combo.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
    return combo


def _draw_hud(canvas, kind, rate, raw_shape, do_enhance, enhancer, do_compare):
    """Telemetry bar: source, FPS, resolution, and enhancement state."""
    h, w = raw_shape[:2]
    label = f"{kind}  {rate:4.1f} FPS  {w}x{h}"
    if do_enhance:
        label += (f"  |  enhance:{enhancer.stats.quality}"
                  f"  {enhancer.stats.last_latency_ms:4.1f}ms")
    else:
        label += "  |  enhance:OFF"
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(canvas, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 255, 0), 2, cv2.LINE_AA)


def _save_snapshot(frame, directory: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(directory, f"snap_{stamp}.jpg")
    cv2.imwrite(path, frame)
    _log.info("Snapshot saved: %s", path)
