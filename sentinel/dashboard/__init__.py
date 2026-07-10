"""Phase 5 — live operations dashboard (FastAPI + WebSocket).

A background pipeline thread (:class:`PipelineRunner`) runs
capture → enhance → detect → dedup → log and publishes annotated frames and
detection events into a thread-safe :class:`LiveState`. The FastAPI app
(:func:`create_app`) serves a plain HTML/JS page and a WebSocket that streams
the latest frame, live events, and stats to every connected browser.

The producer (sync pipeline thread) and consumers (async WebSocket handlers) are
decoupled through ``LiveState``: each client paces itself and naturally drops
frames when slow, so the broadcast is backpressure-safe by construction.
"""

from __future__ import annotations

from .runner import PipelineRunner
from .server import create_app, serve
from .state import LiveState

__all__ = ["LiveState", "PipelineRunner", "create_app", "serve"]
