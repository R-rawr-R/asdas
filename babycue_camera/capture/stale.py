"""Detects when a stream has stopped delivering new frames."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StaleDetector:
    """A feed is stale when no new frame arrived within the threshold.

    The threshold grows for low frame-rate streams so that a phone set to 1 fps is not
    reported as stale between frames.
    """

    base_threshold_s: float = 2.0
    missed_frames: float = 3.0
    max_threshold_s: float = 10.0

    def threshold(self, fps_hint: float | None = None) -> float:
        if fps_hint and fps_hint > 0:
            return min(self.max_threshold_s, max(self.base_threshold_s, self.missed_frames / fps_hint))
        return self.base_threshold_s

    @staticmethod
    def frame_age(last_frame_time: float | None, now: float) -> float | None:
        return None if last_frame_time is None else max(0.0, now - last_frame_time)

    def is_stale(self, last_frame_time: float | None, now: float, fps_hint: float | None = None) -> bool:
        age = self.frame_age(last_frame_time, now)
        return age is not None and age > self.threshold(fps_hint)
