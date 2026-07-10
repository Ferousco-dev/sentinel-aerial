"""Background pipeline thread that feeds the dashboard.

Runs the same capture → enhance → detect → dedup → log pipeline as the preview,
but instead of an OpenCV window it JPEG-encodes each annotated frame and pushes
it — plus any newly logged events and live stats — into a :class:`LiveState`.
"""

from __future__ import annotations

import threading
import time

import cv2

from ..alerts import TelegramNotifier
from ..config import AppConfig
from ..dedup import CooldownFilter
from ..detect import Detector, DetectorUnavailable
from ..enhance import FrameEnhancer
from ..eventlog import EventLog
from ..logging_config import get_logger
from ..scheduler import DetectionScheduler
from ..video import open_source
from ..zones import ZoneMonitor, draw_zones
from .state import LiveState

_log = get_logger("sentinel.dashboard.runner")


class PipelineRunner:
    """Runs the perception pipeline on a daemon thread, publishing to state."""

    def __init__(self, config: AppConfig, state: LiveState) -> None:
        self._cfg = config
        self._state = state
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._encode_params = [
            int(cv2.IMWRITE_JPEG_QUALITY), config.dashboard.jpeg_quality]

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="pipeline", daemon=True)
        self._thread.start()
        _log.info("Pipeline thread started.")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        _log.info("Pipeline thread stopped.")

    # -- worker -------------------------------------------------------------
    def _run(self) -> None:
        cfg = self._cfg
        enhancer = FrameEnhancer(cfg.enhance)
        scheduler: DetectionScheduler | None = None
        detect_ok = cfg.detect_enabled or cfg.log_events
        event_log = EventLog(cfg.log) if cfg.log_events else None
        dedup = (CooldownFilter(cfg.log.cooldown_s)
                 if cfg.log_events and cfg.log.dedup_enabled else None)
        monitor = ZoneMonitor(cfg.zones) if cfg.zones else None
        prev_breached: set[str] = set()
        notifier = (TelegramNotifier.from_env(cfg.alert)
                    if cfg.zones and cfg.alert.enabled else None)

        try:
            source = open_source(
                cfg.discovery, cfg.capture,
                prefer_screen=cfg.prefer_screen, forced_url=cfg.forced_url)
        except RuntimeError as exc:
            _log.error("Dashboard pipeline could not open a source: %s", exc)
            return

        last_t, frames, fps = time.monotonic(), 0, 0.0
        try:
            with source:
                while not self._stop.is_set():
                    ok, raw = source.read()
                    if not ok or raw is None:
                        time.sleep(cfg.capture.read_retry_pause_s)
                        continue

                    frame = enhancer.process(raw) if cfg.enhance_enabled else raw

                    detections, ran = [], False
                    breached: set[str] = set()
                    if detect_ok:
                        if scheduler is None:
                            scheduler = DetectionScheduler(
                                Detector(cfg.detect),
                                min_interval_s=cfg.detect.infer_min_interval_s,
                                every_n=cfg.detect.infer_every_n)
                        try:
                            frame, detections, ran = scheduler.process(frame)
                        except DetectorUnavailable as exc:
                            _log.error("%s", exc)
                            detect_ok = False
                        if event_log is not None and ran and detections:
                            to_log = (dedup.filter(detections)
                                      if dedup is not None else detections)
                            if to_log:
                                event_log.write_many(to_log)
                                self._publish_events(to_log)

                    # Zone-breach evaluation + overlay (every frame, so the red
                    # alert persists while a breach is active).
                    if monitor is not None:
                        breaches = monitor.check(detections, time.time())
                        breached = monitor.breached_zone_names(breaches)
                        frame = draw_zones(frame, cfg.zones, breached)
                        newly = breached - prev_breached
                        if newly:
                            fresh = [b for b in breaches
                                     if b.zone_name in newly]
                            self._publish_breaches(fresh)
                            self._send_alerts(notifier, frame, fresh)
                        prev_breached = breached

                    frames += 1
                    now = time.monotonic()
                    if now - last_t >= 0.5:
                        fps = frames / (now - last_t)
                        frames, last_t = 0, now

                    ok_enc, buf = cv2.imencode(".jpg", frame, self._encode_params)
                    if ok_enc:
                        self._state.publish_frame(
                            buf.tobytes(),
                            self._stats(fps, len(detections), enhancer,
                                        event_log, breached))
        finally:
            if event_log is not None:
                event_log.close()
            if notifier is not None:
                notifier.close()
            self._state.set_running(False)

    def _publish_events(self, detections) -> None:
        stamp = time.time()
        self._state.publish_events([
            {"ts": stamp, "cls_name": d.cls_name,
             "confidence": round(d.confidence, 3), "bbox": list(d.bbox)}
            for d in detections
        ])

    def _send_alerts(self, notifier, frame, breaches) -> None:
        """Fire a Telegram snapshot for each newly-breached zone."""
        if notifier is None:
            return
        for b in breaches:
            caption = (f"🚨 SENTINEL breach\n"
                       f"{b.cls_name} in '{b.zone_name}' "
                       f"({b.overlap * 100:.0f}% overlap)\n"
                       f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
            if notifier.notify_breach(frame, b.zone_name, caption):
                _log.info("Telegram alert queued for zone '%s'.", b.zone_name)

    def _publish_breaches(self, breaches) -> None:
        for b in breaches:
            _log.warning("ZONE BREACH: %s in '%s' (%.0f%% overlap)",
                         b.cls_name, b.zone_name, b.overlap * 100)
        self._state.publish_events([
            {"kind": "breach", "ts": b.ts, "zone": b.zone_name,
             "cls_name": b.cls_name, "confidence": round(b.confidence, 3),
             "overlap": round(b.overlap, 3), "bbox": list(b.bbox)}
            for b in breaches
        ])

    def _stats(self, fps, det_count, enhancer, event_log, breached) -> dict:
        stats = {
            "fps": round(fps, 1),
            "detections": det_count,
            "enhance": (enhancer.stats.quality
                        if self._cfg.enhance_enabled else "off"),
            # Zones currently breached — drives the dashboard alert banner and
            # lets it clear the instant the breach ends.
            "breaches": sorted(breached),
        }
        if event_log is not None:
            summ = event_log.summary()
            stats["total_logged"] = summ.total
            stats["counts_by_class"] = summ.counts_by_class
        return stats
