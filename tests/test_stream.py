"""Integration tests against the local synthetic MJPEG server (no phone, no internet)."""

import socket
import time

import pytest

from babycue_camera.capture import ConnectionState, StaleDetector, StreamWorker
from babycue_camera.config import parse_stream_url
from babycue_camera.devtools.test_pattern_server import TestPatternServer
from babycue_camera.stream import (
    MjpegStream,
    StreamAuthError,
    StreamConnectError,
    StreamHttpError,
    StreamProtocolError,
)
from tests.conftest import wait_until


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_worker(url, **kwargs):
    worker = StreamWorker(parse_stream_url(url), **kwargs)
    worker.start()
    return worker


def test_connects_and_reports_resolution_and_fps(server):
    worker = run_worker(server.url)
    try:
        assert wait_until(lambda: worker.stats().frames_received >= 15)
        stats = worker.stats()
        assert stats.state is ConnectionState.STREAMING
        assert stats.resolution == (640, 480)
        assert 10 < stats.fps < 40
        packet = worker.latest_frame()
        assert packet.image.shape == (480, 640, 3)
        assert not packet.image.flags.writeable
    finally:
        worker.stop()


def test_resolution_updates_when_source_changes(server):
    worker = run_worker(server.url)
    try:
        assert wait_until(lambda: worker.stats().resolution == (640, 480))
        server.width, server.height = 320, 240
        server.drop_connections()  # source restarts with new settings, as when changed in the phone app
        assert wait_until(lambda: worker.stats().resolution == (320, 240))
    finally:
        worker.stop()


def test_refused_connection_fails_with_clear_message():
    worker = run_worker(f"http://127.0.0.1:{free_port()}/video")
    assert wait_until(lambda: worker.stats().state is ConnectionState.FAILED)
    assert "refused" in worker.stats().message.lower()
    assert not wait_until(lambda: worker.is_alive(), timeout=1) or not worker.is_alive()


def test_unreachable_host_times_out():
    # TEST-NET-1 (RFC 5737) is reserved and never routed.
    stream = MjpegStream(parse_stream_url("http://192.0.2.1:8080/video"), connect_timeout=0.5)
    started = time.monotonic()
    with pytest.raises((StreamConnectError, Exception)) as info:
        stream.open()
    assert time.monotonic() - started < 5
    assert "192.0.2.1" in str(info.value)
    stream.close()


def test_wrong_path_reports_404(server):
    stream = MjpegStream(parse_stream_url(server.base_url + "/nope"))
    with pytest.raises(StreamHttpError, match="404"):
        stream.open()
    stream.close()


def test_snapshot_endpoint_is_not_a_stream(server):
    stream = MjpegStream(parse_stream_url(server.base_url + "/shot.jpg"))
    with pytest.raises(StreamProtocolError, match="snapshot"):
        stream.open()
    stream.close()


def test_html_page_is_not_a_stream(server):
    stream = MjpegStream(parse_stream_url(server.base_url + "/"))
    with pytest.raises(StreamProtocolError, match="web page"):
        stream.open()
    stream.close()


def test_malformed_frames_counted_not_displayed(server):
    worker = run_worker(server.base_url + "/garbage")
    try:
        assert wait_until(lambda: worker.stats().frames_malformed >= 5)
        assert worker.latest_frame() is None
        assert worker.stats().frames_received == 0
    finally:
        worker.stop()


def test_authentication():
    with TestPatternServer(username="admin", password="pw") as srv:
        with pytest.raises(StreamAuthError, match="requires"):
            MjpegStream(parse_stream_url(srv.url)).open()
        with pytest.raises(StreamAuthError, match="rejected"):
            MjpegStream(parse_stream_url(srv.url, "admin", "wrong")).open()
        worker = run_worker(srv.url.replace("http://", "http://admin:pw@"))
        try:
            assert wait_until(lambda: worker.stats().frames_received > 0)
        finally:
            worker.stop()


def test_reconnects_after_connection_drop(server):
    worker = run_worker(server.url, reconnect_initial_delay=0.1)
    try:
        assert wait_until(lambda: worker.stats().frames_received > 5)
        server.drop_connections()
        assert wait_until(lambda: worker.stats().reconnect_attempts >= 1)
        before = worker.stats().frames_received
        assert wait_until(lambda: worker.stats().frames_received > before + 5)
        assert worker.stats().state is ConnectionState.STREAMING
    finally:
        worker.stop()


def test_reconnects_after_server_temporarily_unavailable():
    srv = TestPatternServer(fps=25).start()
    port = srv.port
    worker = run_worker(srv.url, reconnect_initial_delay=0.1, reconnect_max_delay=0.3)
    try:
        assert wait_until(lambda: worker.stats().frames_received > 5)
        srv.stop()
        assert wait_until(lambda: worker.stats().state is ConnectionState.RECONNECTING)
        time.sleep(1.0)
        assert worker.stats().state is ConnectionState.RECONNECTING
        srv = TestPatternServer(port=port, fps=25).start()
        before = worker.stats().frames_received
        assert wait_until(lambda: worker.stats().frames_received > before + 5, timeout=8)
        assert worker.stats().state is ConnectionState.STREAMING
    finally:
        worker.stop()
        srv.stop()


def test_stale_feed_detected_when_source_freezes(server):
    detector = StaleDetector(base_threshold_s=0.5)
    worker = run_worker(server.url)
    try:
        assert wait_until(lambda: worker.stats().frames_received > 5)
        stats = worker.stats()
        assert not detector.is_stale(stats.last_frame_time, time.monotonic(), stats.last_good_fps)
        server.pause()
        assert wait_until(
            lambda: detector.is_stale(worker.stats().last_frame_time, time.monotonic(), worker.stats().last_good_fps),
            timeout=3,
        )
        assert wait_until(lambda: worker.stats().fps == 0.0, timeout=3)
        server.resume()
        assert wait_until(lambda: not detector.is_stale(worker.stats().last_frame_time, time.monotonic()), timeout=3)
    finally:
        worker.stop()


def test_stale_threshold_adapts_to_low_fps():
    d = StaleDetector(base_threshold_s=2.0)
    assert d.threshold(30) == 2.0
    assert d.threshold(1) == 3.0
    assert d.threshold(0.01) == d.max_threshold_s
    assert not d.is_stale(None, 100.0)
    assert d.is_stale(90.0, 100.0)


def test_stop_releases_connection_and_buffers(server):
    worker = run_worker(server.url)
    assert wait_until(lambda: server.active_clients == 1 and worker.stats().frames_received > 0)
    started = time.monotonic()
    assert worker.stop(timeout=3)
    assert time.monotonic() - started < 1.0
    assert not worker.is_alive()
    assert worker.latest_frame() is None
    assert worker.stats().state is ConnectionState.STOPPED
    assert wait_until(lambda: server.active_clients == 0)


def test_stop_while_source_frozen_is_fast(server):
    worker = run_worker(server.url)
    assert wait_until(lambda: worker.stats().frames_received > 0)
    server.pause()
    time.sleep(0.2)
    started = time.monotonic()
    assert worker.stop(timeout=3)
    assert time.monotonic() - started < 1.0


def test_only_connects_to_the_stream_host(server, monkeypatch):
    """Streaming must not contact anything except the configured local address."""
    contacted = []
    original = socket.socket.connect

    def recording_connect(self, address):
        contacted.append(address)
        return original(self, address)

    monkeypatch.setattr(socket.socket, "connect", recording_connect)
    worker = run_worker(server.url)
    try:
        assert wait_until(lambda: worker.stats().frames_received > 5)
    finally:
        worker.stop()
    assert contacted
    assert {addr[:2] for addr in contacted} == {("127.0.0.1", server.port)}
