"""Unit tests for the class allowlist filter and CLI overrides (T2)."""

from __future__ import annotations

from sentinel import Detection, filter_by_class
from sentinel.cli import build_config


def _det(name: str, cid: int = 0) -> Detection:
    return Detection(cls_id=cid, cls_name=name, confidence=0.9, bbox=(0, 0, 5, 5))


def test_allowlist_keeps_only_listed_classes():
    dets = [_det("person"), _det("dog", 16), _det("car", 2)]
    kept = filter_by_class(dets, ("person", "car"))
    assert [d.cls_name for d in kept] == ["person", "car"]


def test_none_allowlist_keeps_everything():
    dets = [_det("person"), _det("dog", 16)]
    assert filter_by_class(dets, None) == dets


def test_empty_allowlist_drops_everything():
    dets = [_det("person"), _det("car", 2)]
    assert filter_by_class(dets, ()) == []


def test_empty_detections():
    assert filter_by_class([], ("person",)) == []


def test_cli_default_allowlist():
    cfg = build_config(["--detect"])
    assert "person" in cfg.detect.class_allowlist
    assert "dog" not in cfg.detect.class_allowlist


def test_cli_classes_override():
    cfg = build_config(["--detect", "--classes", "person,dog"])
    assert cfg.detect.class_allowlist == ("person", "dog")


def test_cli_classes_all_disables_filter():
    cfg = build_config(["--detect", "--classes", "all"])
    assert cfg.detect.class_allowlist is None


def test_cli_conf_override():
    cfg = build_config(["--detect", "--conf", "0.6"])
    assert cfg.detect.confidence == 0.6


def test_cli_conf_out_of_range_rejected():
    import pytest
    with pytest.raises((ValueError, SystemExit)):
        build_config(["--detect", "--conf", "1.5"])
