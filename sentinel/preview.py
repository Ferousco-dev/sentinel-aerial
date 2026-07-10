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

from .alerts import TelegramNotifier
from .config import (
    AlertConfig,
    CaptureConfig,
    DetectConfig,
    EnhanceConfig,
    LogConfig,
)
from .dedup import CooldownFilter
from .detect import Detector, DetectorUnavailable
from .scheduler import DetectionScheduler
from .zones import ZoneMonitor, draw_zones
from .enhance import FrameEnhancer
from .eventlog import EventLog
from .logging_config import get_logger
from .video import FrameSource

_log = get_logger("sentinel.preview")

_WINDOW = "Sentinel · feed  [q]uit [s]nap [e]nhance [d]etect [c]ompare"


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
    detect_config: DetectConfig | None = None,
    detect_enabled: bool = False,
    log_config: LogConfig | None = None,
    log_enabled: bool = False,
    zones: tuple = (),
    alert_config: AlertConfig | None = None,
) -> None:
    """Blocking preview loop. Returns when the operator presses ``q``.

    Pipeline per frame: capture → (enhance) → (detect+annotate) → (log) → display.

    Keys: ``q`` quit · ``s`` snapshot · ``e`` toggle enhancement ·
    ``d`` toggle detection · ``c`` toggle raw/enhanced split view.
    """
    os.makedirs(capture_config.snapshot_dir, exist_ok=True)
    cv2.namedWindow(_WINDOW, cv2.WINDOW_NORMAL)
    fps = _FpsMeter()

    enhancer = FrameEnhancer(enhance_config or EnhanceConfig())
    # Detector (and its throttling scheduler) are constructed lazily on first
    # enable so torch/weights are only loaded when detection is requested.
    scheduler: DetectionScheduler | None = None
    detect_cfg = detect_config or DetectConfig()
    # Event log opens only when requested; logging implies detection.
    log_cfg = log_config or LogConfig()
    event_log = EventLog(log_cfg) if log_enabled else None
    # Cooldown de-dup sits between detection and the log writer.
    dedup = (CooldownFilter(log_cfg.cooldown_s)
             if log_enabled and log_cfg.dedup_enabled else None)
    monitor = ZoneMonitor(zones) if zones else None
    prev_breached: set[str] = set()
    alert_cfg = alert_config or AlertConfig()
    notifier = (TelegramNotifier.from_env(alert_cfg)
                if zones and alert_cfg.enabled else None)
    do_enhance = enhance_enabled
    do_detect = detect_enabled or log_enabled
    do_compare = False
    last_count = 0

    _log.info("Preview started · source=%s", source.descriptor)
    _log.info("Keys: q=quit, s=snapshot, e=enhance, d=detect, c=compare")
    if event_log is not None:
        _log.info("Logging detections to %s (session %s)",
                  (log_config or LogConfig()).db_path, event_log.session_id)

    try:
        with source:
            while True:
                ok, raw = source.read()
                if not ok or raw is None:
                    _log.warning("No frame; pausing before retry.")
                    time.sleep(capture_config.read_retry_pause_s)
                    continue

                processed = enhancer.process(raw) if do_enhance else raw

                detections = []
                if do_detect:
                    if scheduler is None:
                        scheduler = DetectionScheduler(
                            Detector(detect_cfg),
                            min_interval_s=detect_cfg.infer_min_interval_s,
                            every_n=detect_cfg.infer_every_n)
                    try:
                        processed, detections, ran = scheduler.process(processed)
                    except DetectorUnavailable as exc:
                        _log.error("%s", exc)
                        do_detect = False  # disable so we don't spam the log
                        ran = False
                    last_count = len(detections)
                    # Only log on frames where inference actually ran — reused
                    # detections are the same objects, not new sightings.
                    if event_log is not None and ran and detections:
                        # Draw all detections, but only log those that clear the
                        # per-class cooldown (or all, if de-dup is disabled).
                        to_log = (dedup.filter(detections)
                                  if dedup is not None else detections)
                        if to_log:
                            event_log.write_many(to_log)

                # Zone-breach overlay + edge-triggered console alert.
                if monitor is not None:
                    breaches = monitor.check(detections, time.time())
                    breached = monitor.breached_zone_names(breaches)
                    processed = draw_zones(processed, zones, breached)
                    for b in breaches:
                        if b.zone_name not in prev_breached:
                            _log.warning(
                                "ZONE BREACH: %s in '%s' (%.0f%% overlap)",
                                b.cls_name, b.zone_name, b.overlap * 100)
                            if notifier is not None:
                                cap = (f"🚨 SENTINEL breach\n{b.cls_name} in "
                                       f"'{b.zone_name}' "
                                       f"({b.overlap * 100:.0f}% overlap)\n"
                                       f"{datetime.now():%Y-%m-%d %H:%M:%S}")
                                notifier.notify_breach(processed, b.zone_name,
                                                       cap)
                    prev_breached = breached

                rate = fps.tick()

                if do_compare and do_enhance:
                    display = _side_by_side(raw, processed)
                else:
                    display = processed.copy()

                _draw_hud(display, source.kind.value, rate, raw.shape,
                          do_enhance, enhancer, do_compare, do_detect,
                          last_count)

                cv2.imshow(_WINDOW, display)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s"):
                    _save_snapshot(processed, capture_config.snapshot_dir)
                elif key == ord("e"):
                    do_enhance = not do_enhance
                    _log.info("Enhancement %s", "ON" if do_enhance else "OFF")
                elif key == ord("d"):
                    do_detect = not do_detect
                    _log.info("Detection %s", "ON" if do_detect else "OFF")
                elif key == ord("c"):
                    do_compare = not do_compare
                    _log.info("Compare view %s",
                              "ON" if do_compare else "OFF")
    finally:
        if event_log is not None:
            summary = event_log.summary()
            _log.info("Session %s logged %d detections across %d class(es).",
                      summary.session_id, summary.total,
                      len(summary.counts_by_class))
            if dedup is not None:
                s = dedup.stats
                _log.info("De-dup: %d seen, %d logged, %d suppressed "
                          "(cooldown %.1fs).",
                          s.seen, s.logged, s.suppressed, log_cfg.cooldown_s)
            event_log.close()
        if notifier is not None:
            notifier.close()
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


def _draw_hud(canvas, kind, rate, raw_shape, do_enhance, enhancer, do_compare,
              do_detect=False, det_count=0):
    """Telemetry bar: source, FPS, resolution, enhancement & detection state."""
    h, w = raw_shape[:2]
    label = f"{kind}  {rate:4.1f} FPS  {w}x{h}"
    if do_enhance:
        label += (f"  |  enhance:{enhancer.stats.quality}"
                  f"  {enhancer.stats.last_latency_ms:4.1f}ms")
    else:
        label += "  |  enhance:OFF"
    label += f"  |  detect:{det_count}" if do_detect else "  |  detect:OFF"
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(canvas, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 255, 0), 2, cv2.LINE_AA)


def _save_snapshot(frame, directory: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(directory, f"snap_{stamp}.jpg")
    cv2.imwrite(path, frame)
    _log.info("Snapshot saved: %s", path)
