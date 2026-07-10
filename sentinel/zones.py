"""Phase 6 (T11) — restricted-zone breach detection.

Given a set of :class:`~sentinel.config.Zone` rectangles and the current frame's
detections, :class:`ZoneMonitor` reports which detections have breached which
zones. A breach is a detection of a zone's ``trigger_classes`` whose bounding box
overlaps the zone rectangle by at least ``min_overlap`` (as a fraction of the
detection's own area — so a person mostly inside the zone counts, a corner clip
can be filtered out).

The overlap maths and breach decision are pure and unit-tested; drawing is a
thin OpenCV helper kept separate so the logic needs no display.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Zone
from .detect import Detection
from .logging_config import get_logger

_log = get_logger("sentinel.zones")

BBox = tuple[int, int, int, int]


def intersection_area(a: BBox, b: BBox) -> int:
    """Area of overlap between two ``(x1, y1, x2, y2)`` boxes (0 if disjoint)."""
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0
    return (ix2 - ix1) * (iy2 - iy1)


def overlap_fraction(det_bbox: BBox, zone_rect: BBox) -> float:
    """Overlap as a fraction of the detection's area, in ``[0, 1]``."""
    det_area = max(1, (det_bbox[2] - det_bbox[0]) * (det_bbox[3] - det_bbox[1]))
    return intersection_area(det_bbox, zone_rect) / det_area


@dataclass(frozen=True)
class BreachEvent:
    """A detection breaching a zone, emitted once per (zone, detection) / frame."""

    zone_name: str
    cls_name: str
    confidence: float
    bbox: BBox
    overlap: float
    ts: float

    def as_row(self) -> dict:
        x1, y1, x2, y2 = self.bbox
        return {
            "zone": self.zone_name,
            "cls_name": self.cls_name,
            "confidence": round(self.confidence, 4),
            "overlap": round(self.overlap, 4),
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        }


class ZoneMonitor:
    """Evaluates detections against a fixed set of zones."""

    def __init__(self, zones: tuple[Zone, ...] | list[Zone]) -> None:
        self._zones = tuple(zones)

    @property
    def zones(self) -> tuple[Zone, ...]:
        return self._zones

    def check(self, detections: list[Detection],
              ts: float) -> list[BreachEvent]:
        """Return every (zone, detection) breach for this frame."""
        breaches: list[BreachEvent] = []
        for zone in self._zones:
            for det in detections:
                if det.cls_name not in zone.trigger_classes:
                    continue
                frac = overlap_fraction(det.bbox, zone.rect)
                breached = (frac > 0 if zone.min_overlap == 0
                            else frac >= zone.min_overlap)
                if breached:
                    breaches.append(BreachEvent(
                        zone_name=zone.name,
                        cls_name=det.cls_name,
                        confidence=det.confidence,
                        bbox=det.bbox,
                        overlap=frac,
                        ts=ts,
                    ))
        return breaches

    def breached_zone_names(self, breaches: list[BreachEvent]) -> set[str]:
        """Names of zones with at least one breach in ``breaches``."""
        return {b.zone_name for b in breaches}


def draw_zones(frame, zones: tuple[Zone, ...] | list[Zone],
               breached: set[str] | None = None):
    """Draw zone rectangles on a copy of ``frame``.

    Breached zones (names in ``breached``) are drawn red with a translucent fill;
    quiet zones are drawn a calm cyan outline. Returns the annotated copy.
    """
    import cv2  # local import keeps the module import cheap

    breached = breached or set()
    out = frame.copy()
    for zone in zones:
        hot = zone.name in breached
        colour = (0, 0, 255) if hot else (200, 200, 0)
        if hot:
            # Translucent red fill to make the alert unmistakable.
            overlay = out.copy()
            cv2.rectangle(overlay, (zone.x1, zone.y1), (zone.x2, zone.y2),
                          colour, -1)
            cv2.addWeighted(overlay, 0.25, out, 0.75, 0, out)
        cv2.rectangle(out, (zone.x1, zone.y1), (zone.x2, zone.y2),
                      colour, 2 if not hot else 3)
        label = f"{zone.name}{'  BREACH' if hot else ''}"
        cv2.putText(out, label, (zone.x1 + 4, zone.y1 + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2, cv2.LINE_AA)
    return out
