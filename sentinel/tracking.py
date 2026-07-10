"""Phase M8 (#40) — multi-object tracking.

Assigns a stable id to each detected object across frames so the system can
count *unique* objects (not per-frame detections), measure how long each has
been present (dwell time), and flag loitering — the structured context the AI
Analyst reasons over.

Implementation: a lightweight IoU tracker (SORT-style association without the
Kalman filter). Each frame's detections are greedily matched to existing tracks
of the same class by bounding-box IoU; unmatched detections start new tracks,
and tracks unseen for ``max_age_frames`` are retired. This is intentionally
pure-Python and torch-free, so it is fast, deterministic, and unit-testable in
CI. It can be swapped for ultralytics' ByteTrack later without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .detect import Detection
from .logging_config import get_logger

_log = get_logger("sentinel.tracking")

BBox = tuple[int, int, int, int]


def iou(a: BBox, b: BBox) -> float:
    """Intersection-over-union of two ``(x1, y1, x2, y2)`` boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    """A single tracked object's state."""

    track_id: int
    cls_name: str
    bbox: BBox
    first_ts: float
    last_ts: float
    hits: int = 1
    missed: int = 0
    loiter_fired: bool = False

    @property
    def dwell_s(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)


@dataclass
class Tracker:
    """IoU tracker producing stable ids, unique counts, and dwell times."""

    iou_threshold: float = 0.3
    max_age_frames: int = 30
    _tracks: dict[int, Track] = field(default_factory=dict)
    _next_id: int = 1
    _unique_by_class: dict[str, int] = field(default_factory=dict)

    def update(self, detections: list[Detection], ts: float) -> list[Detection]:
        """Match ``detections`` to tracks and return them tagged with track ids.

        Returns new Detection copies carrying ``track_id`` in the same order as
        the input.
        """
        # Age all existing tracks by one frame; matched ones reset below.
        for tr in self._tracks.values():
            tr.missed += 1

        # Greedy IoU matching: best pairs first, each track/detection used once.
        candidates = []
        for di, det in enumerate(detections):
            for tid, tr in self._tracks.items():
                if tr.cls_name != det.cls_name:
                    continue
                score = iou(det.bbox, tr.bbox)
                if score >= self.iou_threshold:
                    candidates.append((score, di, tid))
        candidates.sort(reverse=True)

        matched_det: dict[int, int] = {}   # det index -> track id
        used_tracks: set[int] = set()
        for score, di, tid in candidates:
            if di in matched_det or tid in used_tracks:
                continue
            matched_det[di] = tid
            used_tracks.add(tid)

        tagged: list[Detection] = []
        for di, det in enumerate(detections):
            if di in matched_det:
                tid = matched_det[di]
                tr = self._tracks[tid]
                tr.bbox = det.bbox
                tr.last_ts = ts
                tr.hits += 1
                tr.missed = 0
            else:
                tid = self._spawn(det, ts)
            tagged.append(det.with_track_id(tid))

        self._retire()
        return tagged

    def _spawn(self, det: Detection, ts: float) -> int:
        tid = self._next_id
        self._next_id += 1
        self._tracks[tid] = Track(
            track_id=tid, cls_name=det.cls_name, bbox=det.bbox,
            first_ts=ts, last_ts=ts)
        self._unique_by_class[det.cls_name] = (
            self._unique_by_class.get(det.cls_name, 0) + 1)
        return tid

    def _retire(self) -> None:
        stale = [tid for tid, tr in self._tracks.items()
                 if tr.missed > self.max_age_frames]
        for tid in stale:
            del self._tracks[tid]

    # -- queries ------------------------------------------------------------
    @property
    def active_tracks(self) -> list[Track]:
        """Tracks seen on the most recent frame (missed == 0)."""
        return [tr for tr in self._tracks.values() if tr.missed == 0]

    def unique_count(self, cls_name: str | None = None) -> int:
        """Total distinct objects ever seen (optionally for one class)."""
        if cls_name is None:
            return sum(self._unique_by_class.values())
        return self._unique_by_class.get(cls_name, 0)

    def unique_by_class(self) -> dict[str, int]:
        return dict(self._unique_by_class)

    def loitering(self, threshold_s: float) -> list[Track]:
        """Active tracks whose dwell exceeds ``threshold_s`` and haven't yet
        fired a loiter alert. Marks them fired so each loiter is reported once."""
        out = []
        for tr in self.active_tracks:
            if not tr.loiter_fired and tr.dwell_s >= threshold_s:
                tr.loiter_fired = True
                out.append(tr)
        return out
