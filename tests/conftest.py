import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from babycue_camera.devtools.test_pattern_server import TestPatternServer  # noqa: E402


@pytest.fixture
def server():
    srv = TestPatternServer(fps=25).start()
    yield srv
    srv.stop()


def wait_until(predicate, timeout=5.0, interval=0.02):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
