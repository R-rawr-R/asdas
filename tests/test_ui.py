import time

import pytest
from PySide6.QtCore import QElapsedTimer, QSettings, QTimer

from babycue_camera.capture import ConnectionState, StaleDetector
from babycue_camera.ui import MainWindow


@pytest.fixture
def window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings, stale_detector=StaleDetector(base_threshold_s=0.7))
    qtbot.addWidget(win)
    win.show()
    yield win
    win.shutdown()


def test_invalid_url_shows_error_and_does_not_connect(window):
    window.url_edit.setText("rtsp://192.168.1.4/live")
    assert window.connect_stream() is False
    assert "RTSP" in window.message_label.text()
    assert window._worker is None


def test_connect_displays_live_video_and_stats(window, qtbot, server):
    window.url_edit.setText(server.url)
    assert window.connect_stream()
    qtbot.waitUntil(lambda: window.state_label.text() == "Live", timeout=5000)
    qtbot.waitUntil(lambda: window.video.has_image, timeout=5000)
    qtbot.waitUntil(lambda: window.resolution_label.text() == "640 × 480", timeout=5000)
    qtbot.waitUntil(lambda: window.fps_label.text().endswith("fps") and float(window.fps_label.text().split()[0]) > 5)
    qtbot.waitUntil(lambda: window.lighting_label.text() == "OK", timeout=5000)
    assert window.video.overlay_text is None
    assert window.disconnect_button.isEnabled()


def test_stale_overlay_and_recovery(window, qtbot, server):
    window.url_edit.setText(server.url)
    window.connect_stream()
    qtbot.waitUntil(lambda: window.video.has_image, timeout=5000)
    server.pause()
    qtbot.waitUntil(lambda: (window.video.overlay_text or "").startswith("STALE"), timeout=5000)
    assert window.state_label.text() == "Stale (no new frames)"
    qtbot.waitUntil(lambda: window.processing_label.text().startswith("Paused"), timeout=2000)
    server.resume()
    qtbot.waitUntil(lambda: window.video.overlay_text is None, timeout=5000)
    qtbot.waitUntil(lambda: window.state_label.text() == "Live", timeout=2000)


def test_disconnect_clears_frame_and_releases_stream(window, qtbot, server):
    window.url_edit.setText(server.url)
    window.connect_stream()
    qtbot.waitUntil(lambda: window.video.has_image and server.active_clients == 1, timeout=5000)
    window.disconnect_button.click()
    assert not window.video.has_image
    assert window.state_label.text() == "Disconnected"
    assert window.fps_label.text() == "–"
    qtbot.waitUntil(lambda: server.active_clients == 0, timeout=3000)


def test_reconnect_button(window, qtbot, server):
    window.url_edit.setText(server.url)
    window.connect_stream()
    qtbot.waitUntil(lambda: window.state_label.text() == "Live", timeout=5000)
    first = window._worker
    window.reconnect_button.click()
    assert window._worker is not first
    qtbot.waitUntil(lambda: window.state_label.text() == "Live", timeout=5000)
    qtbot.waitUntil(lambda: server.active_clients == 1, timeout=3000)


def test_failed_connection_reported(window, qtbot):
    window.url_edit.setText("http://127.0.0.1:1/video")
    window.connect_stream()
    qtbot.waitUntil(lambda: window.state_label.text() == "Connection failed", timeout=5000)
    assert "refused" in window.message_label.text().lower()
    assert window.reconnect_button.isEnabled()


def test_url_saved_without_password(window, server):
    window.url_edit.setText(server.url.replace("http://", "http://admin:secret@"))
    window.connect_stream()
    saved = str(window._settings.value("stream/url"))
    assert "secret" not in saved and saved == server.url
    assert window.url_edit.text() == server.url


def test_ui_stays_responsive_while_streaming(window, qtbot, server):
    server.fps = 30
    server.width, server.height = 1280, 720
    window.url_edit.setText(server.url)
    window.enhance_checkbox.setChecked(True)
    window.connect_stream()
    qtbot.waitUntil(lambda: window.state_label.text() == "Live", timeout=5000)

    worst = 0
    elapsed = QElapsedTimer()
    elapsed.start()
    last = elapsed.elapsed()

    def probe():
        nonlocal worst, last
        now = elapsed.elapsed()
        worst = max(worst, now - last)
        last = now

    timer = QTimer()
    timer.timeout.connect(probe)
    timer.start(10)
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        qtbot.wait(50)
    timer.stop()
    assert window._worker.stats().state is ConnectionState.STREAMING
    # Event-loop stalls stay well below what a user would perceive as a hang.
    assert worst < 250, f"event loop stalled for {worst} ms"
