"""Unit tests for the per-class cooldown de-duplication filter (T5).

Uses an explicit ``now`` timestamp for each frame so the tests are deterministic
and clock-independent. Runs under pytest, or standalone via ``python -m``.
"""

from __future__ import annotations

from sentinel import CooldownFilter, Detection


def _det(cls_name: str, cls_id: int = 0, conf: float = 0.9) -> Detection:
    return Detection(cls_id=cls_id, cls_name=cls_name,
                     confidence=conf, bbox=(0, 0, 10, 10))


def test_first_sighting_is_logged():
    f = CooldownFilter(cooldown_s=3.0)
    assert [d.cls_name for d in f.filter([_det("person")], now=100.0)] == ["person"]


def test_repeat_within_cooldown_is_suppressed():
    f = CooldownFilter(cooldown_s=3.0)
    f.filter([_det("person")], now=100.0)              # fires
    assert f.filter([_det("person")], now=101.0) == []  # 1s later -> suppressed
    assert f.filter([_det("person")], now=102.9) == []  # still within 3s


def test_repeat_after_cooldown_is_logged_again():
    f = CooldownFilter(cooldown_s=3.0)
    f.filter([_det("person")], now=100.0)
    out = f.filter([_det("person")], now=103.0)         # exactly at cooldown
    assert [d.cls_name for d in out] == ["person"]


def test_whole_frame_of_a_firing_class_passes():
    # Three people in the firing frame -> all three logged (scene captured).
    f = CooldownFilter(cooldown_s=3.0)
    frame = [_det("person"), _det("person"), _det("person")]
    assert len(f.filter(frame, now=100.0)) == 3
    # Next frame within cooldown -> all suppressed.
    assert f.filter([_det("person")], now=100.5) == []


def test_classes_have_independent_cooldowns():
    f = CooldownFilter(cooldown_s=3.0)
    out = f.filter([_det("person"), _det("car", cls_id=2)], now=100.0)
    assert {d.cls_name for d in out} == {"person", "car"}
    # person still cooling down, but a fresh 'truck' fires.
    out2 = f.filter([_det("person"), _det("truck", cls_id=7)], now=101.0)
    assert [d.cls_name for d in out2] == ["truck"]


def test_zero_cooldown_logs_everything():
    f = CooldownFilter(cooldown_s=0.0)
    for t in range(5):
        assert len(f.filter([_det("person")], now=float(t))) == 1


def test_empty_frame_is_noop():
    f = CooldownFilter(cooldown_s=3.0)
    assert f.filter([], now=100.0) == []
    assert f.stats.seen == 0


def test_memory_is_bounded_by_class_vocabulary():
    # Even over many frames, state is keyed by class name only.
    f = CooldownFilter(cooldown_s=3.0)
    for t in range(1000):
        f.filter([_det("person"), _det("car", cls_id=2)], now=float(t) * 0.01)
    assert f.tracked_classes == 2  # never grows beyond distinct classes seen


def test_stats_accounting():
    f = CooldownFilter(cooldown_s=3.0)
    f.filter([_det("person"), _det("person")], now=100.0)  # 2 seen, 2 logged
    f.filter([_det("person")], now=100.5)                  # 1 seen, 1 suppressed
    assert f.stats.seen == 3
    assert f.stats.logged == 2
    assert f.stats.suppressed == 1


def test_negative_cooldown_rejected():
    import pytest
    with pytest.raises(ValueError):
        CooldownFilter(cooldown_s=-1.0)
