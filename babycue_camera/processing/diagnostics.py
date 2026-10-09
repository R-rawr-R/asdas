"""Image-quality diagnostics: brightness, darkness and blur.

These are simple heuristics for the prototype, not infant detection. Thresholds were chosen on
synthetic test images and should be tuned on real nursery footage.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from babycue_camera.processing.base import FrameContext, ProcessorResult


@dataclass(frozen=True)
class QualityThresholds:
    #: Mean grey level (0-255) below which the scene is too dark for reliable analysis.
    dark: float = 50.0
    #: Mean grey level below which the scene is considered very dark.
    very_dark: float = 20.0
    #: Variance of the Laplacian (at ``analysis_width``) below which the image is called blurry.
    blur: float = 100.0
    #: Grey-level standard deviation below which blur cannot be judged (flat or featureless scene).
    min_contrast: float = 12.0
    analysis_width: int = 320


def brightness(gray: np.ndarray) -> float:
    return float(gray.mean())


def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian; higher means more edge detail."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def to_analysis_gray(frame: np.ndarray, width: int) -> np.ndarray:
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    if w > width:
        gray = cv2.resize(gray, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    return gray


class ImageQualityProcessor:
    name = "image_quality"

    def __init__(self, thresholds: QualityThresholds | None = None):
        self.thresholds = thresholds or QualityThresholds()

    def process(self, frame: np.ndarray, context: FrameContext) -> ProcessorResult:
        t = self.thresholds
        gray = to_analysis_gray(frame, t.analysis_width)
        mean = brightness(gray)
        contrast = float(gray.std())
        sharp = sharpness(gray)

        too_dark = mean < t.dark
        if mean < t.very_dark:
            lighting = "very dark"
        elif too_dark:
            lighting = "too dark"
        else:
            lighting = "ok"

        blur_reliable = not too_dark and contrast >= t.min_contrast
        blurry: bool | None = (sharp < t.blur) if blur_reliable else None

        warnings = []
        if too_dark:
            warnings.append("Too dark for reliable visual analysis")
        if blurry:
            warnings.append("Image appears blurry")
        if blurry is None:
            warnings.append("Blur not assessed (scene too dark or featureless)")

        return ProcessorResult(
            self.name,
            ok=not too_dark and blurry is False,
            summary=f"Brightness {mean:.0f}/255, {lighting}",
            values={
                "brightness": mean,
                "contrast": contrast,
                "sharpness": sharp,
                "lighting": lighting,
                "too_dark": too_dark,
                "blurry": blurry,
            },
            warnings=tuple(warnings),
        )
