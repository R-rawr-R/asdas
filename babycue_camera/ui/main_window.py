"""Main BabyCue camera viewer window."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from babycue_camera.capture import ConnectionState, StaleDetector, StreamStats, StreamWorker
from babycue_camera.config import InvalidStreamUrl, StreamTarget, parse_stream_url
from babycue_camera.processing import ImageQualityProcessor, ProcessingPipeline, ProcessingRunner, enhance_low_light
from babycue_camera.processing.runner import ProcessingSnapshot
from babycue_camera.ui.video_view import VideoView, bgr_to_qimage

log = logging.getLogger(__name__)

UI_REFRESH_MS = 33
_STATE_TEXT = {
    ConnectionState.IDLE: "Not connected",
    ConnectionState.CONNECTING: "Connecting…",
    ConnectionState.STREAMING: "Live",
    ConnectionState.RECONNECTING: "Reconnecting…",
    ConnectionState.FAILED: "Connection failed",
    ConnectionState.STOPPED: "Disconnected",
}
_STALE_COLOR = QColor(255, 190, 0)
_ERROR_COLOR = QColor(255, 90, 90)


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: QSettings | None = None,
        *,
        stale_detector: StaleDetector | None = None,
        worker_options: dict | None = None,
    ):
        super().__init__()
        self.setWindowTitle("BabyCue Camera Viewer")
        self.resize(1200, 720)
        self._settings = settings if settings is not None else QSettings("BabyCue", "CameraViewer")
        self._stale = stale_detector or StaleDetector()
        self._worker_options = worker_options or {}
        self._worker: StreamWorker | None = None
        self._runner: ProcessingRunner | None = None
        self._retired: list[tuple[StreamWorker, ProcessingRunner | None]] = []
        self._target: StreamTarget | None = None
        self._shown_seq = -1
        self._shown_enhanced = False
        self._last_state: ConnectionState | None = None

        self._build_ui()
        self.url_edit.setText(str(self._settings.value("stream/url", "")))
        self.user_edit.setText(str(self._settings.value("stream/username", "")))
        self._apply_state(ConnectionState.IDLE, "Enter the stream URL shown by the phone camera app.")

        self._timer = QTimer(self)
        self._timer.setInterval(UI_REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- layout -----------------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "Stream URL or IP, e.g. http://192.168.1.25:8080/video (shown in the phone app)"
        )
        self.url_edit.returnPressed.connect(self.connect_stream)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Username (optional)")
        self.user_edit.setMaximumWidth(160)
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("Password (optional)")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setMaximumWidth(160)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_stream)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self.disconnect_stream)
        self.reconnect_button = QPushButton("Reconnect")
        self.reconnect_button.clicked.connect(self.reconnect_stream)

        top = QHBoxLayout()
        top.addWidget(QLabel("Stream:"))
        top.addWidget(self.url_edit, 1)
        top.addWidget(self.user_edit)
        top.addWidget(self.password_edit)
        top.addWidget(self.connect_button)
        top.addWidget(self.disconnect_button)
        top.addWidget(self.reconnect_button)

        self.video = VideoView()

        self.state_label = QLabel()
        self.state_label.setTextFormat(Qt.TextFormat.PlainText)
        self.address_label = QLabel("–")
        self.address_label.setWordWrap(True)
        self.resolution_label = QLabel("–")
        self.fps_label = QLabel("–")
        self.age_label = QLabel("–")
        self.frames_label = QLabel("–")
        connection_box = QGroupBox("Connection")
        form = QFormLayout(connection_box)
        form.addRow("Status:", self.state_label)
        form.addRow("Address:", self.address_label)
        form.addRow("Resolution:", self.resolution_label)
        form.addRow("Frame rate:", self.fps_label)
        form.addRow("Last frame:", self.age_label)
        form.addRow("Frames:", self.frames_label)

        self.brightness_label = QLabel("–")
        self.lighting_label = QLabel("–")
        self.sharpness_label = QLabel("–")
        self.processing_label = QLabel("Idle")
        self.processing_label.setWordWrap(True)
        self.ai_label = QLabel("Infant detection: not implemented")
        self.ai_label.setWordWrap(True)
        self.enhance_checkbox = QCheckBox("Low-light display enhancement")
        self.enhance_checkbox.setToolTip(
            "Software brightening (gamma + CLAHE) applied to the displayed copy only.\n"
            "This is not infrared night vision and can increase visible noise.\n"
            "Diagnostics always use the original frame."
        )
        diag_box = QGroupBox("Camera processing")
        diag = QFormLayout(diag_box)
        diag.addRow("Brightness:", self.brightness_label)
        diag.addRow("Lighting:", self.lighting_label)
        diag.addRow("Sharpness:", self.sharpness_label)
        diag.addRow("Pipeline:", self.processing_label)
        diag.addRow(self.ai_label)
        diag.addRow(self.enhance_checkbox)

        side = QVBoxLayout()
        side.addWidget(connection_box)
        side.addWidget(diag_box)
        side.addStretch(1)
        side_widget = QWidget()
        side_widget.setLayout(side)
        side_widget.setFixedWidth(330)

        middle = QHBoxLayout()
        middle.addWidget(self.video, 1)
        middle.addWidget(side_widget)

        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        root = QVBoxLayout()
        root.addLayout(top)
        root.addLayout(middle, 1)
        root.addWidget(self.message_label)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

    # -- actions ----------------------------------------------------------------------------

    def connect_stream(self) -> bool:
        try:
            target = parse_stream_url(
                self.url_edit.text(), self.user_edit.text().strip() or None, self.password_edit.text() or None
            )
        except InvalidStreamUrl as exc:
            self._show_message(str(exc), error=True)
            return False

        self._stop_current()
        self._target = target
        self.url_edit.setText(target.url)
        self._settings.setValue("stream/url", target.url)
        self._settings.setValue("stream/username", target.username or "")

        self._worker = StreamWorker(target, **self._worker_options)
        pipeline = ProcessingPipeline([ImageQualityProcessor()])
        self._runner = ProcessingRunner(self._worker.latest_frame, pipeline)
        self._worker.start()
        self._runner.start()
        self._shown_seq = -1
        self.video.clear("Connecting…")
        self.address_label.setText(target.url)
        if target.is_local_address:
            self._show_message(f"Connecting to {target.url}")
        else:
            self._show_message(
                f"Warning: {target.host} is not a local network address. The video stream is unencrypted "
                "HTTP; only connect to devices on your own network.",
                error=True,
            )
        self._last_state = None
        self._tick()
        return True

    def disconnect_stream(self) -> None:
        had_worker = self._worker is not None
        self._stop_current()
        self.video.clear("Disconnected")
        self._reset_readouts()
        self._apply_state(ConnectionState.STOPPED, "Disconnected" if had_worker else "Not connected")

    def reconnect_stream(self) -> None:
        self.connect_stream()

    def _stop_current(self) -> None:
        worker, runner = self._worker, self._runner
        self._worker = self._runner = None
        if worker is None:
            return
        # Non-blocking: aborting the socket unblocks the capture thread almost immediately.
        worker.stop(timeout=0)
        if runner is not None:
            runner.stop(timeout=0)
        self._retired.append((worker, runner))
        self._retired = [(w, r) for w, r in self._retired if w.is_alive()]

    def shutdown(self, timeout: float = 3.0) -> None:
        self._timer.stop()
        self._stop_current()
        deadline = time.monotonic() + timeout
        for worker, runner in self._retired:
            worker.stop(timeout=max(0.0, deadline - time.monotonic()))
            if runner is not None:
                runner.stop(timeout=max(0.0, deadline - time.monotonic()))
        self._retired.clear()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    # -- periodic update --------------------------------------------------------------------

    def _tick(self) -> None:
        worker = self._worker
        if worker is None:
            return
        stats = worker.stats()
        now = time.monotonic()

        packet = worker.latest_frame()
        enhanced = self.enhance_checkbox.isChecked()
        if packet is not None and (packet.seq != self._shown_seq or enhanced != self._shown_enhanced):
            frame = enhance_low_light(packet.image) if enhanced else packet.image
            self.video.set_image(bgr_to_qimage(frame))
            self.video.set_badge("Enhanced display (not night vision)" if enhanced else None)
            self._shown_seq, self._shown_enhanced = packet.seq, enhanced

        stale = stats.state is ConnectionState.STREAMING and self._stale.is_stale(
            stats.last_frame_time, now, stats.last_good_fps
        )
        self._update_readouts(stats, now)
        self._update_overlay(stats, now)
        if stats.state is not self._last_state:
            self._apply_state(stats.state, stats.message)
        elif stats.state is ConnectionState.RECONNECTING:
            self._show_message(stats.message, error=True)
        if stats.state is ConnectionState.STREAMING:
            self.state_label.setText("Stale (no new frames)" if stale else _STATE_TEXT[stats.state])

        if self._runner is not None:
            self._update_processing(self._runner.latest(), stats, stale)

    def _update_readouts(self, stats: StreamStats, now: float) -> None:
        if stats.resolution:
            self.resolution_label.setText(f"{stats.resolution[0]} × {stats.resolution[1]}")
        self.fps_label.setText(f"{stats.fps:.1f} fps" if stats.state is ConnectionState.STREAMING else "–")
        age = self._stale.frame_age(stats.last_frame_time, now)
        self.age_label.setText("–" if age is None else f"{age:.1f} s ago")
        malformed = f" ({stats.frames_malformed} malformed)" if stats.frames_malformed else ""
        self.frames_label.setText(f"{stats.frames_received}{malformed}")

    def _update_overlay(self, stats: StreamStats, now: float) -> None:
        if not self.video.has_image:
            return
        age = self._stale.frame_age(stats.last_frame_time, now)
        if stats.state is ConnectionState.FAILED:
            self.video.set_overlay("NOT LIVE — connection failed", _ERROR_COLOR)
        elif stats.state is ConnectionState.RECONNECTING:
            self.video.set_overlay(f"NOT LIVE — reconnecting\nlast frame {age or 0:.0f} s ago", _STALE_COLOR)
        elif self._stale.is_stale(stats.last_frame_time, now, stats.last_good_fps):
            self.video.set_overlay(f"STALE FEED — no new frames for {age:.0f} s", _STALE_COLOR)
        else:
            self.video.set_overlay(None)

    def _update_processing(self, snapshot: ProcessingSnapshot | None, stats: StreamStats, stale: bool) -> None:
        if stats.state is not ConnectionState.STREAMING or stale:
            self.processing_label.setText("Paused (no live frames)")
            return
        if snapshot is None:
            self.processing_label.setText("Waiting for frames…")
            return
        result = snapshot.get("image_quality")
        if result is None or result.error:
            self.processing_label.setText(f"Image-quality check failed: {result.error if result else 'n/a'}")
            return
        v = result.values
        self.brightness_label.setText(f"{v['brightness']:.0f} / 255")
        self.lighting_label.setText(
            {"ok": "OK", "too dark": "Too dark for reliable analysis", "very dark": "Very dark"}[v["lighting"]]
        )
        blurry = v["blurry"]
        sharp = f"{v['sharpness']:.0f}"
        self.sharpness_label.setText(
            f"{sharp} (not assessed)" if blurry is None else f"{sharp} ({'blurry' if blurry else 'OK'})"
        )
        self.processing_label.setText(f"Image-quality diagnostics active ({result.duration_ms:.0f} ms/frame)")

    # -- helpers ----------------------------------------------------------------------------

    def _apply_state(self, state: ConnectionState, message: str) -> None:
        self._last_state = state
        self.state_label.setText(_STATE_TEXT[state])
        active = state in (ConnectionState.CONNECTING, ConnectionState.STREAMING, ConnectionState.RECONNECTING)
        self.disconnect_button.setEnabled(active)
        self.reconnect_button.setEnabled(self._target is not None)
        if state is ConnectionState.FAILED:
            self._show_message(message, error=True)
            self.processing_label.setText("Stopped")
        elif state is ConnectionState.STREAMING:
            self._show_message(message)
        elif state is ConnectionState.RECONNECTING:
            self._show_message(message, error=True)
        elif state in (ConnectionState.CONNECTING, ConnectionState.STOPPED, ConnectionState.IDLE):
            self._show_message(message)

    def _reset_readouts(self) -> None:
        for label in (
            self.resolution_label,
            self.fps_label,
            self.age_label,
            self.frames_label,
            self.brightness_label,
            self.lighting_label,
            self.sharpness_label,
        ):
            label.setText("–")
        self.processing_label.setText("Idle")

    def _show_message(self, text: str, *, error: bool = False) -> None:
        self.message_label.setText(text)
        self.message_label.setStyleSheet("color: #d33;" if error else "")
