"""Unit tests for restricted-zone breach detection (T11)."""

from __future__ import annotations

from sentinel import Detection, Zone, ZoneMonitor
from sentinel.cli import build_config
from sentinel.zones import intersection_area, overlap_fraction


def _det(name, bbox, conf=0.9):
    return Detection(cls_id=0, cls_name=name, confidence=conf, bbox=bbox)


# -- geometry ---------------------------------------------------------------
def test_intersection_area():
    assert intersection_area((0, 0, 10, 10), (5, 5, 15, 15)) == 25
    assert intersection_area((0, 0, 10, 10), (20, 20, 30, 30)) == 0
    assert intersection_area((0, 0, 10, 10), (10, 0, 20, 10)) == 0  # edge touch


def test_overlap_fraction():
    # detection area 100; half inside -> 0.5
    assert overlap_fraction((0, 0, 10, 10), (5, 0, 100, 100)) == 0.5
    assert overlap_fraction((0, 0, 10, 10), (50, 50, 60, 60)) == 0.0


# -- breach decisions -------------------------------------------------------
def test_person_breaches_overlapping_zone():
    zone = Zone("north", 50, 50, 150, 150)
    mon = ZoneMonitor([zone])
    breaches = mon.check([_det("person", (100, 100, 200, 200))], ts=1.0)
    assert len(breaches) == 1
    assert breaches[0].zone_name == "north" and breaches[0].overlap > 0


def test_no_breach_when_disjoint():
    mon = ZoneMonitor([Zone("z", 0, 0, 50, 50)])
    assert mon.check([_det("person", (200, 200, 260, 260))], ts=1.0) == []


def test_non_trigger_class_ignored():
    mon = ZoneMonitor([Zone("z", 0, 0, 100, 100)])  # triggers on person only
    assert mon.check([_det("car", (10, 10, 40, 40))], ts=1.0) == []


def test_custom_trigger_class():
    zone = Zone("road", 0, 0, 100, 100, trigger_classes=("car", "truck"))
    mon = ZoneMonitor([zone])
    out = mon.check([_det("car", (10, 10, 40, 40)),
                     _det("person", (10, 10, 40, 40))], ts=1.0)
    assert [b.cls_name for b in out] == ["car"]


def test_min_overlap_threshold():
    # Zone tiny relative to the person; only 4% of the person's box overlaps.
    zone = Zone("gate", 0, 0, 10, 10, min_overlap=0.5)
    mon = ZoneMonitor([zone])
    assert mon.check([_det("person", (0, 0, 50, 50))], ts=1.0) == []  # 4% < 50%
    zone2 = Zone("gate", 0, 0, 40, 50, min_overlap=0.5)  # 64% overlap
    assert len(ZoneMonitor([zone2]).check(
        [_det("person", (0, 0, 50, 50))], ts=1.0)) == 1


def test_multiple_zones_and_breached_names():
    mon = ZoneMonitor([Zone("a", 0, 0, 100, 100), Zone("b", 500, 500, 600, 600)])
    breaches = mon.check([_det("person", (10, 10, 40, 40))], ts=1.0)
    assert mon.breached_zone_names(breaches) == {"a"}


def test_zone_validation():
    import pytest
    with pytest.raises(ValueError):
        Zone("bad", 100, 100, 50, 50)  # x2 < x1
    with pytest.raises(ValueError):
        Zone("bad", 0, 0, 10, 10, min_overlap=2.0)


# -- CLI parsing ------------------------------------------------------------
def test_cli_zone_parsing():
    cfg = build_config(["--detect", "--zone", "10,20,110,120:north",
                        "--zone", "0,0,50,50"])
    assert len(cfg.zones) == 2
    assert cfg.zones[0].name == "north" and cfg.zones[0].rect == (10, 20, 110, 120)
    assert cfg.zones[1].name.startswith("zone")


def test_cli_zone_bad_spec_rejected():
    import pytest
    with pytest.raises(SystemExit):
        build_config(["--zone", "1,2,3"])  # too few coords
