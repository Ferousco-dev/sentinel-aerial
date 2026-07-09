"""Transport-agnostic frame sources.

Every downstream stage consumes a :class:`FrameSource`; none of them know or
care whether frames originate from a network stream or a screen region. Sources
are context managers and iterables that yield ``numpy`` BGR frames.
"""

from __future__ import annotations

import abc
import time
from typing import Iterator

import cv2
import numpy as np

from ..config import CaptureConfig, DiscoveryConfig, SourceKind
from ..logging_config import get_logger
from .discovery import DiscoveryResult, StreamDiscovery

_log = get_logger("sentinel.video.source")

Frame = np.ndarray


class FrameSource(abc.ABC):
    """Abstract base for anything that yields BGR frames.

    Contract:
        * :meth:`read` returns ``(ok, frame)``; ``frame`` is a BGR ``ndarray``.
        * Iterating the source yields frames and stops on unrecoverable failure.
        * Instances are context managers that release their resources on exit.
    """

    kind: SourceKind

    @abc.abstractmethod
    def read(self) -> tuple[bool, Frame | None]:
        """Grab a single frame."""

    @abc.abstractmethod
    def release(self) -> None:
        """Free underlying resources. Idempotent."""

    @property
    def descriptor(self) -> str:
        """Human-readable identity for logs and the dashboard."""
        return self.kind.value

    # -- iterator / context-manager sugar -----------------------------------
    def __iter__(self) -> Iterator[Frame]:
        return self

    def __next__(self) -> Frame:
        ok, frame = self.read()
        if not ok or frame is None:
            raise StopIteration
        return frame

    def __enter__(self) -> "FrameSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class StreamSource(FrameSource):
    """A network video stream with transparent reconnection.

    Toy-drone WiFi is lossy; the decoder frequently returns empty reads or drops
    the connection outright. This source retries transient empty reads and, on a
    hard failure, reopens the URL with bounded exponential backoff so the rest of
    the pipeline sees an uninterrupted frame stream.
    """

    kind = SourceKind.STREAM

    def __init__(
        self,
        result: DiscoveryResult,
        capture_config: CaptureConfig,
    ) -> None:
        self._cfg = capture_config
        self._url = result.url
        self._cap: cv2.VideoCapture | None = result.capture
        self.width = result.width
        self.height = result.height

    @property
    def descriptor(self) -> str:
        return f"{self.kind.value} · {self._url}"

    def read(self) -> tuple[bool, Frame | None]:
        if self._cap is None:
            return False, None

        ok, frame = self._cap.read()
        if ok and frame is not None and frame.size > 0:
            return True, frame

        # One brief pause absorbs a transient empty read without a full reconnect.
        time.sleep(self._cfg.read_retry_pause_s)
        ok, frame = self._cap.read()
        if ok and frame is not None and frame.size > 0:
            return True, frame

        _log.warning("Read failed on %s; attempting reconnect.", self._url)
        if self._reconnect():
            ok, frame = self._cap.read()  # type: ignore[union-attr]
            if ok and frame is not None and frame.size > 0:
                return True, frame
        return False, None

    def _reconnect(self) -> bool:
        self._release_capture()
        for attempt in range(1, self._cfg.reconnect_attempts + 1):
            backoff = self._cfg.reconnect_backoff_s * attempt
            _log.info("Reconnect attempt %d/%d in %.1fs…",
                      attempt, self._cfg.reconnect_attempts, backoff)
            time.sleep(backoff)
            cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
            if cap.isOpened():
                ok, frame = cap.read()
                if ok and frame is not None and frame.size > 0:
                    self._cap = cap
                    _log.info("Reconnected to %s.", self._url)
                    return True
            cap.release()
        _log.error("Reconnection to %s failed after %d attempts.",
                   self._url, self._cfg.reconnect_attempts)
        return False

    def _release_capture(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def release(self) -> None:
        self._release_capture()


class ScreenSource(FrameSource):
    """Desktop-region capture via ``mss`` — the universal fallback.

    Works with *any* drone: mirror the vendor phone app to the laptop and capture
    that window. Guarantees the demo always has a live feed even when the stream
    URL is locked down or the vendor uses a proprietary UDP handshake.
    """

    kind = SourceKind.SCREEN

    def __init__(self, capture_config: CaptureConfig) -> None:
        try:
            import mss  # imported lazily so a stream-only host needs no mss
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "mss is required for screen capture. Install it: pip install mss"
            ) from exc

        self._sct = mss.mss()
        region = capture_config.screen_region
        if region is None:
            self._monitor = self._sct.monitors[
                capture_config.screen_monitor_index
            ]
        else:
            left, top, width, height = region
            self._monitor = {
                "left": left, "top": top, "width": width, "height": height,
            }
        self.width = int(self._monitor["width"])
        self.height = int(self._monitor["height"])

    @property
    def descriptor(self) -> str:
        return (f"{self.kind.value} · "
                f"{self.width}x{self.height} @ "
                f"({self._monitor['left']},{self._monitor['top']})")

    def read(self) -> tuple[bool, Frame | None]:
        shot = self._sct.grab(self._monitor)
        frame = cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)
        return True, frame

    def release(self) -> None:
        try:
            self._sct.close()
        except Exception:  # pragma: no cover - close is best-effort
            pass


def open_source(
    discovery_config: DiscoveryConfig,
    capture_config: CaptureConfig,
    *,
    prefer_screen: bool = False,
    forced_url: str | None = None,
) -> FrameSource:
    """Resolve a concrete :class:`FrameSource` using the priority ladder:

    1. ``prefer_screen`` → screen capture immediately.
    2. ``forced_url`` → try that exact URL, then fall through on failure.
    3. Active discovery across the candidate search space.
    4. Screen-capture fallback.
    """
    if prefer_screen:
        _log.info("Screen capture selected explicitly.")
        return ScreenSource(capture_config)

    discovery = StreamDiscovery(discovery_config)

    if forced_url:
        _log.info("Trying explicit URL: %s", forced_url)
        result = discovery.probe_url(forced_url)
        if result is not None:
            return StreamSource(result, capture_config)
        _log.warning("Explicit URL failed; continuing to discovery.")

    result = discovery.discover()
    if result is not None:
        return StreamSource(result, capture_config)

    _log.info("Falling back to screen capture — mirror the drone app on screen.")
    return ScreenSource(capture_config)
