"""Runs the processing pipeline on its own thread, decoupled from capture and UI."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from babycue_camera.capture.worker import FramePacket
from babycue_camera.processing.base import FrameContext, ProcessingPipeline, ProcessorResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingSnapshot:
    seq: int
    timestamp: float
    results: tuple[ProcessorResult, ...]

    def get(self, processor: str) -> ProcessorResult | None:
        return next((r for r in self.results if r.processor == processor), None)


class ProcessingRunner:
    """Pulls the newest frame from ``frame_source`` at most every ``interval_s`` seconds.

    Frames that arrive while processing is busy are skipped, so slow analysis never delays capture.
    """

    def __init__(
        self,
        frame_source: Callable[[], FramePacket | None],
        pipeline: ProcessingPipeline,
        *,
        interval_s: float = 0.2,
    ):
        self._source = frame_source
        self.pipeline = pipeline
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: ProcessingSnapshot | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="babycue-processing", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 2.0) -> None:
        self._stop.set()
        if self._thread is not None and timeout != 0:
            self._thread.join(timeout)
        with self._lock:
            self._latest = None

    def latest(self) -> ProcessingSnapshot | None:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        last_seq = -1
        while not self._stop.wait(self.interval_s):
            packet = self._source()
            if packet is None or packet.seq == last_seq:
                continue
            last_seq = packet.seq
            context = FrameContext(packet.seq, packet.timestamp, packet.width, packet.height)
            results = self.pipeline.run(packet.image, context)
            with self._lock:
                if not self._stop.is_set():
                    self._latest = ProcessingSnapshot(packet.seq, packet.timestamp, tuple(results))
