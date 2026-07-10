"""Phase 3 — object detection (T1).

Runs YOLOv8n on each (enhanced) frame and returns **structured** detections so
downstream stages (Phase 4 logging, Phase 6 zone-breach) consume data, not
pixels. The annotated frame is produced separately for the preview/dashboard.

Design notes:

* **Lazy load.** ``ultralytics``/``torch`` are heavy and the weights download on
  first use, so the model is loaded on the first ``detect`` call — never at
  import. Importing :mod:`sentinel` stays instant and dependency-light.
* **Graceful absence.** If ``ultralytics`` is not installed, the failure is
  raised once as a clear :class:`DetectorUnavailable` with an install hint, and
  logged — the rest of the package still imports and runs.
* **Deterministic output.** :class:`Detection` is a plain dataclass with integer
  pixel bbox coordinates, ready to serialize into SQLite in Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import DetectConfig
from .logging_config import get_logger

_log = get_logger("sentinel.detect")

Frame = np.ndarray


class DetectorUnavailable(RuntimeError):
    """Raised when the detection backend (ultralytics/torch) cannot be loaded."""


@dataclass(frozen=True)
class Detection:
    """One detected object in a single frame.

    Attributes:
        cls_id: COCO class index.
        cls_name: Human-readable class label (e.g. ``"person"``).
        confidence: Detector confidence in ``[0, 1]``.
        bbox: Pixel coordinates ``(x1, y1, x2, y2)`` — top-left, bottom-right.
    """

    cls_id: int
    cls_name: str
    confidence: float
    bbox: tuple[int, int, int, int]

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)

    def as_row(self) -> dict:
        """Flat dict for logging / serialization (Phase 4)."""
        x1, y1, x2, y2 = self.bbox
        return {
            "cls_id": self.cls_id,
            "cls_name": self.cls_name,
            "confidence": round(self.confidence, 4),
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        }


# A stable colour per class id so the same class keeps its colour across frames.
# Generated deterministically (no per-frame randomness) for a calm display.
def _class_colour(cls_id: int) -> tuple[int, int, int]:
    # Golden-ratio hue spacing → visually distinct BGR without a lookup table.
    hue = (cls_id * 0.61803398875) % 1.0
    r = int(255 * abs((hue * 6 + 0) % 2 - 1))
    g = int(255 * abs((hue * 6 + 4) % 2 - 1))
    b = int(255 * abs((hue * 6 + 2) % 2 - 1))
    return (b, g, r)


class Detector:
    """Stateful YOLOv8n detector. One instance per stream (holds the model)."""

    def __init__(self, config: DetectConfig | None = None) -> None:
        self._cfg = config or DetectConfig()
        self._model = None          # loaded lazily
        self._names: dict[int, str] = {}

    # -- lifecycle ----------------------------------------------------------
    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            _log.error("ultralytics not installed — detection unavailable.")
            raise DetectorUnavailable(
                "Detection requires ultralytics. Install it with:\n"
                "    pip install ultralytics\n"
                "(pulls in PyTorch; the YOLOv8n weights are ~6 MB, "
                "downloaded on first use)."
            ) from exc

        _log.info("Loading detector '%s' on device=%s (first-use weight "
                  "download may take a moment)…",
                  self._cfg.model_name, self._cfg.device or "auto")
        self._model = YOLO(self._cfg.model_name)
        # model.names maps class-id -> label; cache it for structured output.
        self._names = dict(self._model.names)
        _log.info("Detector ready: %d classes.", len(self._names))

    @property
    def class_names(self) -> dict[int, str]:
        """COCO id → label map (empty until the model has loaded)."""
        return dict(self._names)

    # -- inference ----------------------------------------------------------
    def detect(self, frame: Frame) -> list[Detection]:
        """Run detection on one BGR frame and return structured detections."""
        self._ensure_model()
        results = self._model.predict(   # type: ignore[union-attr]
            frame,
            conf=self._cfg.confidence,
            iou=self._cfg.iou,
            device=self._cfg.device,
            max_det=self._cfg.max_detections,
            verbose=False,
        )
        if not results:
            return []

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []

        # Pull tensors to CPU/numpy once, then build plain dataclasses.
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)

        detections: list[Detection] = []
        for (x1, y1, x2, y2), conf, cls_id in zip(xyxy, confs, clss):
            detections.append(Detection(
                cls_id=int(cls_id),
                cls_name=self._names.get(int(cls_id), str(int(cls_id))),
                confidence=float(conf),
                bbox=(int(x1), int(y1), int(x2), int(y2)),
            ))
        return detections

    # -- rendering ----------------------------------------------------------
    def annotate(self, frame: Frame, detections: list[Detection]) -> Frame:
        """Draw labelled, confidence-scored boxes on a copy of ``frame``."""
        import cv2  # local import keeps module import cheap and cv2-optional

        out = frame.copy()
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            colour = _class_colour(det.cls_id)
            cv2.rectangle(out, (x1, y1), (x2, y2), colour,
                          self._cfg.box_thickness)

            label = f"{det.cls_name} {det.confidence:.0%}"
            (tw, th), baseline = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, self._cfg.font_scale, 1)
            # Filled label chip above the box (or inside if it would clip the top).
            top = max(0, y1 - th - baseline - 4)
            cv2.rectangle(out, (x1, top), (x1 + tw + 4, top + th + baseline + 4),
                          colour, -1)
            cv2.putText(out, label, (x1 + 2, top + th + 2),
                        cv2.FONT_HERSHEY_SIMPLEX, self._cfg.font_scale,
                        (0, 0, 0), 1, cv2.LINE_AA)
        return out

    def process(self, frame: Frame) -> tuple[Frame, list[Detection]]:
        """Convenience: detect and annotate in one call.

        Returns ``(annotated_frame, detections)``.
        """
        detections = self.detect(frame)
        return self.annotate(frame, detections), detections
