import cv2
import numpy as np
import pytest

from babycue_camera.devtools.test_pattern_server import render_pattern
from babycue_camera.processing import (
    FrameContext,
    ImageQualityProcessor,
    ProcessingPipeline,
    ProcessorResult,
    enhance_low_light,
)

CTX = FrameContext(seq=1, timestamp=0.0, width=640, height=480)


def scene(brightness=1.0, blur=0):
    return render_pattern(3, brightness=brightness, blur=blur)


def quality(frame):
    return ImageQualityProcessor().process(frame, CTX)


def test_well_lit_sharp_scene_is_ok():
    r = quality(scene())
    assert r.values["lighting"] == "ok"
    assert r.values["blurry"] is False
    assert r.ok
    assert not r.warnings


@pytest.mark.parametrize("factor,expected", [(0.4, "too dark"), (0.12, "very dark")])
def test_dark_scene_detected(factor, expected):
    r = quality(scene(brightness=factor))
    assert r.values["too_dark"]
    assert r.values["lighting"] == expected
    assert r.values["blurry"] is None  # blur is not judged on dark frames
    assert "Too dark for reliable visual analysis" in r.warnings


@pytest.mark.parametrize("blur", [6, 12])
def test_blurry_scene_detected(blur):
    r = quality(scene(blur=blur))
    assert r.values["lighting"] == "ok"
    assert r.values["blurry"] is True
    assert "Image appears blurry" in r.warnings


def test_featureless_scene_blur_not_assessed():
    flat = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert quality(flat).values["blurry"] is None


def test_brightness_measurement_is_accurate():
    for level in (10, 60, 200):
        frame = np.full((100, 100, 3), level, dtype=np.uint8)
        assert quality(frame).values["brightness"] == pytest.approx(level, abs=1)


def test_enhancement_returns_new_brighter_frame_and_keeps_original():
    dark = scene(brightness=0.15)
    original = dark.copy()
    out = enhance_low_light(dark)
    assert out is not dark
    assert np.array_equal(dark, original)
    assert cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).mean() > cv2.cvtColor(dark, cv2.COLOR_BGR2GRAY).mean() * 1.5


def test_enhancement_does_not_overbrighten_normal_frames():
    frame = scene()
    out = enhance_low_light(frame)
    assert abs(float(out.mean()) - float(frame.mean())) < 40


class Mutator:
    name = "mutator"

    def process(self, frame, context):
        frame[0, 0] = 0
        return ProcessorResult(self.name, True, "mutated")


class Exploder:
    name = "exploder"

    def process(self, frame, context):
        raise RuntimeError("boom")


def test_pipeline_isolates_failures_and_protects_frame():
    frame = scene()
    frame[0, 0] = 255
    results = ProcessingPipeline([Mutator(), Exploder(), ImageQualityProcessor()]).run(frame, CTX)
    assert [r.processor for r in results] == ["mutator", "exploder", "image_quality"]
    assert not results[0].ok and "read-only" in results[0].error
    assert not results[1].ok and results[1].error == "boom"
    assert results[2].values["lighting"] == "ok"
    assert (frame[0, 0] == 255).all()
