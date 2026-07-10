"""Unit tests for the inference throttling scheduler (T3).

A fake detector (counts calls, returns a call-tagged detection) lets us verify
throttling deterministically with an injected clock — no torch/weights needed.
"""

from __future__ import annotations

import numpy as np

from sentinel import DetectionScheduler
from sentinel.detect import Detection


class _FakeDetector:
    """Duck-typed stand-in for Detector: counts detect() calls."""

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        # bbox x2 encodes the call number so tests can detect cache reuse.
        return [Detection(0, "person", 0.9, (0, 0, self.calls, self.calls))]

    def annotate(self, frame, detections):
        return frame


_FRAME = np.zeros((8, 8, 3), np.uint8)


def test_first_frame_always_runs():
    d = _FakeDetector()
    s = DetectionScheduler(d, min_interval_s=1.0)
    _, dets, ran = s.process(_FRAME, now=0.0)
    assert ran and d.calls == 1 and dets[0].bbox[2] == 1


def test_reuse_within_interval():
    d = _FakeDetector()
    s = DetectionScheduler(d, min_interval_s=1.0)
    s.process(_FRAME, now=0.0)                      # runs (call 1)
    _, dets, ran = s.process(_FRAME, now=0.5)       # within interval
    assert not ran and d.calls == 1                 # no new inference
    assert dets[0].bbox[2] == 1                      # reused cache


def test_runs_again_after_interval():
    d = _FakeDetector()
    s = DetectionScheduler(d, min_interval_s=1.0)
    s.process(_FRAME, now=0.0)
    _, dets, ran = s.process(_FRAME, now=1.0)       # exactly at interval
    assert ran and d.calls == 2 and dets[0].bbox[2] == 2


def test_interval_bounds_inference_rate():
    # 100 frames across ~1s at a 0.15s interval -> runs at 0,.15,.30,...,.90 = 7.
    d = _FakeDetector()
    s = DetectionScheduler(d, min_interval_s=0.15)
    for i in range(100):
        s.process(_FRAME, now=i * 0.01)
    assert d.calls == 7
    assert s.stats.frames == 100
    assert s.stats.inferences == 7
    assert s.stats.reuses == 93


def test_frame_based_gate_when_interval_zero():
    d = _FakeDetector()
    s = DetectionScheduler(d, min_interval_s=0.0, every_n=3)
    ran_flags = [s.process(_FRAME, now=float(i))[2] for i in range(7)]
    # frame 1 is forced, then it fires whenever frame_idx % 3 == 0 (idx 3, 6).
    assert ran_flags == [True, False, True, False, False, True, False]
    assert d.calls == 3


def test_every_frame_when_no_throttle():
    d = _FakeDetector()
    s = DetectionScheduler(d, min_interval_s=0.0, every_n=1)
    for i in range(10):
        s.process(_FRAME, now=float(i))
    assert d.calls == 10  # no skipping


def test_infer_ratio_stat():
    d = _FakeDetector()
    s = DetectionScheduler(d, min_interval_s=0.15)
    for i in range(100):
        s.process(_FRAME, now=i * 0.01)
    assert abs(s.stats.infer_ratio - 0.07) < 1e-9


def test_validation():
    import pytest
    with pytest.raises(ValueError):
        DetectionScheduler(_FakeDetector(), min_interval_s=-1.0)
    with pytest.raises(ValueError):
        DetectionScheduler(_FakeDetector(), every_n=0)
