"""Sentinel — AI Aerial Surveillance & Response System.

A software-only surveillance brain for closed, non-programmable WiFi FPV toy
drones. The laptop ingests the drone's video feed, enhances it, runs on-device
AI detection, logs events, and serves a live operations dashboard.

The package is built as a pipeline of composable stages:

    ingest  ->  enhance  ->  detect  ->  log  ->  serve  ->  alert  ->  report

This module (Phase 1) implements ``ingest``: a transport-agnostic video source
abstraction with active stream discovery and a resilient screen-capture
fallback.
"""

from __future__ import annotations

from .config import (
    CaptureConfig,
    DetectConfig,
    DiscoveryConfig,
    EnhanceConfig,
    EnhanceQuality,
    LogConfig,
    SourceKind,
)
from .dedup import CooldownFilter, DedupStats
from .detect import Detection, Detector, DetectorUnavailable
from .scheduler import DetectionScheduler, ScheduleStats
from .enhance import FrameEnhancer, benchmark, enhance_frame
from .eventlog import EventLog, SessionSummary
from .video import (
    FrameSource,
    ScreenSource,
    StreamSource,
    StreamDiscovery,
    open_source,
)

__all__ = [
    "CaptureConfig",
    "DetectConfig",
    "DiscoveryConfig",
    "EnhanceConfig",
    "EnhanceQuality",
    "LogConfig",
    "SourceKind",
    "Detector",
    "Detection",
    "DetectorUnavailable",
    "CooldownFilter",
    "DedupStats",
    "DetectionScheduler",
    "ScheduleStats",
    "EventLog",
    "SessionSummary",
    "FrameEnhancer",
    "enhance_frame",
    "benchmark",
    "FrameSource",
    "ScreenSource",
    "StreamSource",
    "StreamDiscovery",
    "open_source",
]

__version__ = "0.1.0"
