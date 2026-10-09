"""Conservative low-light enhancement for display only.

This is software brightening, not infrared night vision: it cannot recover detail the sensor did
not capture and it amplifies noise. The input frame is never modified; a new array is returned so
the original stays available for analysis.
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def enhance_low_light(
    frame: np.ndarray,
    *,
    target_brightness: float = 110.0,
    min_gamma: float = 0.45,
    clahe_clip_limit: float = 2.0,
    clahe_grid: int = 8,
) -> np.ndarray:
    """Return a brightened copy using gamma correction plus CLAHE on the luminance channel."""
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    mean = float(l_channel.mean())
    gamma = 1.0
    if 1.0 < mean < target_brightness:
        gamma = max(min_gamma, math.log(target_brightness / 255.0) / math.log(mean / 255.0))
    if gamma < 1.0:
        lut = np.array([((i / 255.0) ** gamma) * 255.0 for i in range(256)], dtype=np.uint8)
        l_channel = cv2.LUT(l_channel, lut)

    clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit, tileGridSize=(clahe_grid, clahe_grid))
    l_channel = clahe.apply(l_channel)
    return cv2.cvtColor(cv2.merge((l_channel, a_channel, b_channel)), cv2.COLOR_LAB2BGR)
