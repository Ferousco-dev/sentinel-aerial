"""Active discovery of a toy-drone video stream.

Strategy: TCP-probe the small set of candidate hosts/ports first (milliseconds),
then run a real ``cv2.VideoCapture`` against the surviving URLs and require a
genuine decoded frame before declaring success — ``VideoCapture.isOpened()``
returns ``True`` for several dead toy streams, so it cannot be trusted alone.
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass

import cv2

from ..config import DiscoveryConfig
from ..logging_config import get_logger

_log = get_logger("sentinel.video.discovery")


@dataclass
class DiscoveryResult:
    """A successfully opened capture together with the URL that produced it."""

    capture: "cv2.VideoCapture"
    url: str
    width: int
    height: int


class StreamDiscovery:
    """Locates the first working video stream within a :class:`DiscoveryConfig`."""

    def __init__(self, config: DiscoveryConfig) -> None:
        self._cfg = config

    # -- public API ---------------------------------------------------------
    def discover(self) -> DiscoveryResult | None:
        """Return the first working stream, or ``None`` if nothing responds."""
        _log.info("Scanning %d candidate host(s) for open media ports…",
                  len(self._cfg.candidate_ips))
        live_ips = self._live_ips()
        if live_ips:
            _log.info("Hosts answering a media port: %s", ", ".join(live_ips))
        else:
            _log.info("No hosts answered a port probe; trying all candidates.")

        urls = self._cfg.iter_urls(live_ips)
        _log.info("Attempting %d URL candidate(s)…", len(urls))

        for url in urls:
            result = self._try_open(url)
            if result is not None:
                _log.info("Stream acquired: %s  (%dx%d)",
                          result.url, result.width, result.height)
                return result

        _log.warning("Discovery exhausted; no direct stream found.")
        return None

    def probe_url(self, url: str) -> DiscoveryResult | None:
        """Attempt a single explicit URL (used by the ``--url`` fast path)."""
        return self._try_open(url)

    # -- internals ----------------------------------------------------------
    def _live_ips(self) -> list[str]:
        live: list[str] = []
        for ip in self._cfg.candidate_ips:
            for port in self._cfg.probe_ports:
                if self._port_open(ip, port):
                    _log.debug("Open port %s:%d", ip, port)
                    live.append(ip)
                    break
        return live

    def _port_open(self, ip: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(self._cfg.port_timeout_s)
                return sock.connect_ex((ip, port)) == 0
        except OSError:
            return False

    def _try_open(self, url: str) -> DiscoveryResult | None:
        _log.debug("Opening %s", url)

        # Prefer TCP transport for RTSP to avoid packet-loss artefacts on flaky
        # drone WiFi. This env var is read by the FFMPEG backend at open time.
        if url.startswith("rtsp://"):
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

        capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not capture.isOpened():
            capture.release()
            return None

        deadline = time.monotonic() + self._cfg.first_frame_timeout_s
        while time.monotonic() < deadline:
            ok, frame = capture.read()
            if ok and frame is not None and frame.size > 0:
                height, width = frame.shape[:2]
                return DiscoveryResult(capture, url, width, height)
            time.sleep(0.1)

        _log.debug("Opened but produced no frames: %s", url)
        capture.release()
        return None
