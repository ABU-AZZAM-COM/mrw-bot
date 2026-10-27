"""Local image screening and confidence classification with NudeNet."""
from __future__ import annotations

import os
import logging
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_detector: Any = None
_detector_lock = threading.Lock()
SCREENING_THRESHOLD = 0.40

EXPOSED_LABELS = {
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "MALE_BREAST_EXPOSED",
}

EXPLICIT_LABELS = {
    "ANUS_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
}


@dataclass(frozen=True)
class ScreeningResult:
    flagged: bool
    review_required: bool = False
    labels: tuple[str, ...] = ()
    max_score: float = 0.0
    error: str = ""


def is_enabled() -> bool:
    return True


def threshold() -> float:
    return SCREENING_THRESHOLD


def review_threshold() -> float:
    return SCREENING_THRESHOLD


def _get_detector() -> Any:
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                try:
                    from nudenet import NudeDetector
                except ImportError as exc:
                    raise RuntimeError(
                        "NSFW screening is enabled but NudeNet is not installed"
                    ) from exc
                _detector = NudeDetector()
    return _detector


def _scan_paths(path: str | Path, detector: Any) -> list[dict[str, Any]]:
    """Scan the original and up to four overlapping local crops for small details."""
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise ValueError("image could not be decoded for local moderation")

    height, width = image.shape[:2]
    detections = list(detector.detect(str(path)))
    if max(width, height) < 640:
        return detections

    tile_width = min(width, max(1, round(width * 0.65)))
    tile_height = min(height, max(1, round(height * 0.65)))
    x_starts = sorted({0, max(0, width - tile_width)})
    y_starts = sorted({0, max(0, height - tile_height)})

    with tempfile.TemporaryDirectory(prefix="mrw-nsfw-") as temp_dir:
        tile_index = 0
        for y in y_starts:
            for x in x_starts:
                if tile_width == width and tile_height == height:
                    continue
                tile = image[y:y + tile_height, x:x + tile_width]
                tile_path = Path(temp_dir) / f"tile-{tile_index}.jpg"
                if not cv2.imwrite(str(tile_path), tile):
                    raise RuntimeError("could not prepare local moderation crop")
                detections.extend(detector.detect(str(tile_path)))
                tile_index += 1
    return detections


def screen_image(path: str | Path) -> ScreeningResult:
    """Run local multi-scale inference and separate review from high-confidence exposure."""
    logger.debug("Screening uploaded image locally")
    detected: dict[str, float] = {}
    cutoff = threshold()
    review_cutoff = review_threshold()
    for detection in _scan_paths(path, _get_detector()):
        label = str(detection.get("class") or "").upper()
        score = float(detection.get("score") or 0)
        if label in EXPLICIT_LABELS | EXPOSED_LABELS and score >= review_cutoff:
            detected[label] = max(score, detected.get(label, 0.0))
    review = tuple(sorted(detected))
    high_confidence_exposure = any(score > cutoff for score in detected.values())
    return ScreeningResult(
        flagged=high_confidence_exposure,
        review_required=bool(review),
        labels=review,
        max_score=max(detected.values(), default=0.0),
    )
