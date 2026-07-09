"""Video ingest subsystem: discovery and transport-agnostic frame sources."""

from __future__ import annotations

from .discovery import DiscoveryResult, StreamDiscovery
from .source import FrameSource, ScreenSource, StreamSource, open_source

__all__ = [
    "DiscoveryResult",
    "StreamDiscovery",
    "FrameSource",
    "ScreenSource",
    "StreamSource",
    "open_source",
]
