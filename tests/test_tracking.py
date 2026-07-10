"""Unit tests for the IoU multi-object tracker (M8 #40)."""

from __future__ import annotations

from sentinel import Detection, Track, Tracker
from sentinel.tracking import iou


def _det(name, bbox, cid=0):
    return Detection(cls_id=cid, cls_name=name, confidence=0.9, bbox=bbox)


def test_iou_basic():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert abs(iou((0, 0, 10, 10), (5, 0, 15, 10)) - (50 / 150)) < 1e-9


def test_same_object_keeps_id_across_frames():
    t = Tracker(iou_threshold=0.3)
    a = t.update([_det("person", (0, 0, 20, 40))], ts=1.0)
    b = t.update([_det("person", (2, 1, 22, 41))], ts=2.0)   # moved slightly
    assert a[0].track_id == b[0].track_id
    assert t.unique_count("person") == 1


def test_new_object_gets_new_id():
    t = Tracker(iou_threshold=0.3)
    t.update([_det("person", (0, 0, 20, 40))], ts=1.0)
    out = t.update([_det("person", (0, 0, 20, 40)),
                    _det("person", (200, 200, 220, 240))], ts=2.0)
    ids = {d.track_id for d in out}
    assert len(ids) == 2
    assert t.unique_count("person") == 2


def test_class_switch_does_not_reuse_id():
    t = Tracker(iou_threshold=0.3)
    p = t.update([_det("person", (0, 0, 20, 40))], ts=1.0)
    c = t.update([_det("car", (0, 0, 20, 40), cid=2)], ts=2.0)  # same box, diff class
    assert p[0].track_id != c[0].track_id
    assert t.unique_count("person") == 1 and t.unique_count("car") == 1


def test_dwell_time_accumulates():
    t = Tracker()
    t.update([_det("person", (0, 0, 20, 40))], ts=100.0)
    t.update([_det("person", (1, 1, 21, 41))], ts=105.0)
    tr = t.active_tracks[0]
    assert tr.dwell_s == 5.0


def test_track_retired_after_max_age():
    t = Tracker(iou_threshold=0.3, max_age_frames=2)
    t.update([_det("person", (0, 0, 20, 40))], ts=1.0)   # id 1
    for i in range(4):
        t.update([], ts=2.0 + i)                         # nobody seen
    # After > max_age missed frames the track is retired; a reappearance is new.
    out = t.update([_det("person", (0, 0, 20, 40))], ts=10.0)
    assert out[0].track_id != 1
    assert t.unique_count("person") == 2


def test_loitering_fires_once():
    t = Tracker()
    t.update([_det("person", (0, 0, 20, 40))], ts=0.0)
    assert t.loitering(threshold_s=5.0) == []            # dwell 0
    t.update([_det("person", (1, 0, 21, 40))], ts=6.0)   # dwell 6
    loiters = t.loitering(threshold_s=5.0)
    assert len(loiters) == 1 and isinstance(loiters[0], Track)
    # Doesn't fire again for the same track.
    t.update([_det("person", (1, 0, 21, 40))], ts=9.0)
    assert t.loitering(threshold_s=5.0) == []


def test_unique_by_class_and_total():
    t = Tracker(iou_threshold=0.3)
    t.update([_det("person", (0, 0, 20, 40)),
              _det("car", (300, 0, 340, 40), cid=2)], ts=1.0)
    t.update([_det("person", (500, 500, 520, 540))], ts=2.0)  # a 2nd person
    assert t.unique_by_class() == {"person": 2, "car": 1}
    assert t.unique_count() == 3


def test_config_validation():
    import pytest
    from sentinel import TrackConfig
    with pytest.raises(ValueError):
        TrackConfig(iou_threshold=0.0)
    with pytest.raises(ValueError):
        TrackConfig(iou_threshold=1.5)
