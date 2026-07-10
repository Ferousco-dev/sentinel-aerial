"""Unit tests for the post-flight report generator (T15)."""

from __future__ import annotations

import os
import tempfile

from sentinel import Detection, EventLog, LogConfig, generate_report, list_sessions
from sentinel.report import build_report_html, load_events


def _seed_db():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "events.db")
    with EventLog(LogConfig(db_path=db), session_id="sess_a") as log:
        log.write_many([Detection(0, "person", 0.9, (0, 0, 10, 10))], ts=1000.0)
        log.write_many([Detection(2, "car", 0.8, (5, 5, 20, 20))], ts=1010.0)
        log.write_many([Detection(0, "person", 0.7, (1, 1, 9, 9))], ts=1030.0)
    with EventLog(LogConfig(db_path=db), session_id="sess_b") as log:
        log.write_many([Detection(0, "person", 0.6, (0, 0, 5, 5))], ts=2000.0)
    return db, d


def test_list_sessions_orders_recent_first():
    db, _ = _seed_db()
    sessions = list_sessions(db)
    assert [s.session_id for s in sessions] == ["sess_b", "sess_a"]
    a = next(s for s in sessions if s.session_id == "sess_a")
    assert a.total == 3 and a.duration_s == 30.0


def test_load_events_for_session():
    db, _ = _seed_db()
    events = load_events(db, "sess_a")
    assert len(events) == 3
    assert [e["cls_name"] for e in events] == ["person", "car", "person"]
    assert events[0]["ts"] == 1000.0  # oldest first


def test_build_html_contains_summary_and_counts():
    db, _ = _seed_db()
    info = next(s for s in list_sessions(db) if s.session_id == "sess_a")
    html = build_report_html(info, load_events(db, "sess_a"))
    assert "SENTINEL" in html and "Post-Flight Report" in html
    assert "sess_a" in html
    assert ">3<" in html          # total detections card
    assert "person" in html and "car" in html
    assert "<svg" in html          # timeline + bar chart rendered


def test_generate_writes_latest_session_by_default():
    db, d = _seed_db()
    out = os.path.join(d, "report.html")
    path = generate_report(db, out)
    assert os.path.exists(path)
    content = open(path, encoding="utf-8").read()
    assert "sess_b" in content     # most recent chosen by default


def test_generate_specific_session():
    db, d = _seed_db()
    out = os.path.join(d, "r.html")
    generate_report(db, out, session_id="sess_a")
    assert "sess_a" in open(out, encoding="utf-8").read()


def test_generate_unknown_session_raises():
    import pytest
    db, d = _seed_db()
    with pytest.raises(ValueError):
        generate_report(db, os.path.join(d, "x.html"), session_id="nope")
