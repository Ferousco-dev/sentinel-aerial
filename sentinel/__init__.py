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
    DiscoveryConfig,
    EnhanceConfig,
    EnhanceQuality,
    SourceKind,
)
from .enhance import FrameEnhancer, benchmark, enhance_frame
from .video import (
    FrameSource,
    ScreenSource,
    StreamSource,
    StreamDiscovery,
    open_source,
)

__all__ = [
    "CaptureConfig",
    "DiscoveryConfig",
    "EnhanceConfig",
    "EnhanceQuality",
    "SourceKind",
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
