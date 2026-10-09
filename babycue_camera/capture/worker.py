"""Background capture thread: connects, decodes frames, tracks statistics and reconnects."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np

from babycue_camera.config import StreamTarget
from babycue_camera.stream import MjpegStream, StreamError

log = logging.getLogger(__name__)


class ConnectionState(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class FramePacket:
    """A decoded BGR frame. ``image`` must be treated as read-only by consumers."""

    image: np.ndarray
    seq: int
    timestamp: float

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass(frozen=True)
class StreamStats:
    state: ConnectionState
    message: str
    frames_received: int
    frames_malformed: int
    fps: float
    last_good_fps: float
    resolution: tuple[int, int] | None
    last_frame_time: float | None
    reconnect_attempts: int


StreamFactory = Callable[[StreamTarget], "MjpegStream"]


class StreamWorker:
    """Runs one stream connection on a daemon thread.

    Only the most recent frame is kept: slow consumers skip frames instead of building a backlog.
    Initial connection failures are reported as FAILED (the user fixes the address and reconnects);
    once streaming has worked, retryable interruptions trigger automatic reconnection with backoff.
    """

    def __init__(
        self,
        target: StreamTarget,
        *,
        auto_reconnect: bool = True,
        retry_initial_connect: bool = False,
        reconnect_initial_delay: float = 0.5,
        reconnect_max_delay: float = 10.0,
        fps_window_s: float = 2.0,
        stream_factory: StreamFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.target = target
        self.auto_reconnect = auto_reconnect
        self.retry_initial_connect = retry_initial_connect
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.fps_window_s = fps_window_s
        self._stream_factory = stream_factory or (lambda t: MjpegStream(t))
        self._clock = clock

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream: MjpegStream | None = None

        self._state = ConnectionState.IDLE
        self._message = "Not connected"
        self._latest: FramePacket | None = None
        self._seq = 0
        self._malformed = 0
        self._frame_times: deque[float] = deque()
        self._last_good_fps = 0.0
        self._attempts = 0

    # -- public API -------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("StreamWorker can only be started once")
        self._thread = threading.Thread(target=self._run, name="babycue-capture", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 3.0) -> bool:
        """Stop streaming and release the connection. Returns True if the thread has exited."""
        self._stop.set()
        with self._lock:
            stream = self._stream
        if stream is not None:
            stream.abort()
        joined = True
        if self._thread is not None and timeout != 0:
            self._thread.join(timeout)
            joined = not self._thread.is_alive()
        with self._lock:
            self._latest = None
            self._frame_times.clear()
            if self._state not in (ConnectionState.FAILED,):
                self._state, self._message = ConnectionState.STOPPED, "Disconnected"
        return joined

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def latest_frame(self) -> FramePacket | None:
        with self._lock:
            return self._latest

    def stats(self) -> StreamStats:
        now = self._clock()
        with self._lock:
            self._trim_frame_times(now)
            times = self._frame_times
            fps = 0.0
            if len(times) >= 2 and times[-1] > times[0]:
                interval = (times[-1] - times[0]) / (len(times) - 1)
                # Report 0 once several expected frames have been missed, instead of a stale rate.
                if now - times[-1] <= max(3 * interval, 0.5):
                    fps = 1.0 / interval
            if fps > 0:
                self._last_good_fps = fps
            latest = self._latest
            return StreamStats(
                state=self._state,
                message=self._message,
                frames_received=self._seq,
                frames_malformed=self._malformed,
                fps=fps,
                last_good_fps=self._last_good_fps,
                resolution=(latest.width, latest.height) if latest is not None else None,
                last_frame_time=latest.timestamp if latest is not None else None,
                reconnect_attempts=self._attempts,
            )

    # -- thread -----------------------------------------------------------------------------

    def _run(self) -> None:
        has_streamed = False
        attempt = 0
        log.info("Connecting to %s", self.target.url)
        while not self._stop.is_set():
            self._set_state(
                ConnectionState.CONNECTING if attempt == 0 else ConnectionState.RECONNECTING,
                f"Connecting to {self.target.url}…" if attempt == 0 else f"Reconnecting (attempt {attempt})…",
            )
            error: StreamError | None = None
            stream = self._stream_factory(self.target)
            with self._lock:
                self._stream = stream
            try:
                if self._stop.is_set():
                    break
                stream.open()
                for jpeg in stream.frames():
                    if self._stop.is_set():
                        break
                    if self._publish(jpeg):
                        if attempt or not has_streamed:
                            log.info("Streaming from %s", self.target.url)
                        has_streamed = True
                        attempt = 0
            except StreamError as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - keep the thread alive and report
                log.exception("Unexpected capture error")
                error = StreamError(f"Unexpected error: {exc}")
            finally:
                stream.close()
                with self._lock:
                    self._stream = None

            if self._stop.is_set():
                break
            if error is None:
                error = StreamError("The stream ended.")
            retry = self.auto_reconnect and error.retryable and (has_streamed or self.retry_initial_connect)
            if not retry:
                log.warning("Stream failed: %s", error)
                self._set_state(ConnectionState.FAILED, str(error))
                return
            attempt += 1
            with self._lock:
                self._attempts += 1
            delay = min(self.reconnect_max_delay, self.reconnect_initial_delay * 2 ** (attempt - 1))
            log.warning("Stream interrupted: %s; retrying in %.1fs", error, delay)
            self._set_state(
                ConnectionState.RECONNECTING, f"Connection lost: {error} Retrying in {delay:.1f} s (attempt {attempt})."
            )
            self._stop.wait(delay)
        self._set_state(ConnectionState.STOPPED, "Disconnected")
        log.info("Capture stopped")

    def _publish(self, jpeg: bytes) -> bool:
        if self._stop.is_set():
            return False
        image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            with self._lock:
                self._malformed += 1
            return False
        image.flags.writeable = False
        now = self._clock()
        with self._lock:
            if self._stop.is_set():
                return False
            self._seq += 1
            self._latest = FramePacket(image=image, seq=self._seq, timestamp=now)
            self._frame_times.append(now)
            self._trim_frame_times(now)
            if self._state is not ConnectionState.STREAMING:
                self._state, self._message = ConnectionState.STREAMING, f"Streaming from {self.target.url}"
        return True

    def _trim_frame_times(self, now: float) -> None:
        cutoff = now - self.fps_window_s
        while self._frame_times and self._frame_times[0] < cutoff:
            self._frame_times.popleft()

    def _set_state(self, state: ConnectionState, message: str) -> None:
        with self._lock:
            if self._state is ConnectionState.STOPPED and state is not ConnectionState.STOPPED:
                return
            self._state, self._message = state, message
