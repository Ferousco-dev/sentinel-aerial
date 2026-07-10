"""Integration tests for the dashboard server (T6).

Drives the FastAPI app with a static LiveState (no pipeline thread) via
TestClient, checking the HTTP routes and that the WebSocket streams the latest
frame and live events.
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from sentinel import AppConfig, DashboardConfig
from sentinel.dashboard import LiveState, create_app


def _client(state: LiveState) -> TestClient:
    cfg = AppConfig(dashboard=DashboardConfig(push_interval_s=0.01))
    return TestClient(create_app(cfg, state=state, runner=None))


def test_index_serves_dashboard_html():
    client = _client(LiveState())
    r = client.get("/")
    assert r.status_code == 200
    assert "SENTINEL" in r.text and "/ws" in r.text


def test_healthz():
    client = _client(LiveState())
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_state_frame_snapshot_and_events():
    state = LiveState()
    assert state.frame_snapshot() == (0, None, {})
    state.publish_frame(b"jpeg-bytes", {"fps": 12.5})
    seq, jpeg, stats = state.frame_snapshot()
    assert seq == 1 and jpeg == b"jpeg-bytes" and stats["fps"] == 12.5

    state.publish_events([{"cls_name": "person", "confidence": 0.9}])
    events = state.events_since(0)
    assert len(events) == 1 and events[0]["id"] == 1
    assert state.events_since(1) == []  # nothing newer than id 1


def test_websocket_streams_frame_and_event():
    state = LiveState()
    state.publish_frame(b"\xff\xd8\xff-fake-jpeg", {"fps": 20, "detections": 2})
    state.publish_events([{"cls_name": "car", "confidence": 0.81, "bbox": [1, 2, 3, 4]}])

    client = _client(state)
    with client.websocket_connect("/ws") as ws:
        got_frame = got_event = False
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "frame":
                assert base64.b64decode(msg["jpg"]) == b"\xff\xd8\xff-fake-jpeg"
                assert msg["stats"]["detections"] == 2
                got_frame = True
            elif msg["type"] == "event":
                assert msg["cls_name"] == "car" and msg["id"] == 1
                got_event = True
            if got_frame and got_event:
                break
    assert got_frame and got_event


def test_websocket_breach_event_and_alert_stats():
    state = LiveState()
    # Frame stats carry the active breach list; the UI uses it to show/clear.
    state.publish_frame(b"\xff\xd8\xff", {"fps": 15, "breaches": ["north"]})
    state.publish_events([
        {"kind": "breach", "zone": "north", "cls_name": "person",
         "confidence": 0.9, "ts": 1.0, "bbox": [1, 2, 3, 4]},
    ])
    client = _client(state)
    with client.websocket_connect("/ws") as ws:
        breach_evt = frame_breaches = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "event" and msg.get("kind") == "breach":
                breach_evt = msg
            elif msg["type"] == "frame":
                frame_breaches = msg["stats"].get("breaches")
            if breach_evt and frame_breaches is not None:
                break
    assert breach_evt["zone"] == "north"
    assert frame_breaches == ["north"]


def test_dashboard_config_validation():
    import pytest
    with pytest.raises(ValueError):
        DashboardConfig(port=0)
    with pytest.raises(ValueError):
        DashboardConfig(jpeg_quality=0)
