"""Pluggable frame-processing interface.

Future modules (infant visibility detection, tracking, local AI inference) implement
:class:`FrameProcessor` and are added to a :class:`ProcessingPipeline`. Processors receive a
read-only view of the original decoded frame, so they cannot alter what other processors or the
display see; any processor that needs a modified image must work on its own copy.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameContext:
    seq: int
    timestamp: float
    width: int
    height: int


@dataclass(frozen=True)
class ProcessorResult:
    processor: str
    ok: bool
    summary: str
    values: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error: str | None = None
    duration_ms: float = 0.0


@runtime_checkable
class FrameProcessor(Protocol):
    name: str

    def process(self, frame: np.ndarray, context: FrameContext) -> ProcessorResult: ...


class ProcessingPipeline:
    """Runs processors in order, isolating failures so one module cannot break the others."""

    def __init__(self, processors: Iterable[FrameProcessor] = ()):
        self._processors: list[FrameProcessor] = list(processors)

    @property
    def processors(self) -> tuple[FrameProcessor, ...]:
        return tuple(self._processors)

    def add(self, processor: FrameProcessor) -> None:
        self._processors.append(processor)

    def run(self, frame: np.ndarray, context: FrameContext) -> list[ProcessorResult]:
        view = frame.view()
        view.flags.writeable = False
        results = []
        for processor in self._processors:
            started = time.perf_counter()
            try:
                result = processor.process(view, context)
            except Exception as exc:  # noqa: BLE001 - isolate faulty processors
                log.exception("Processor %s failed", processor.name)
                result = ProcessorResult(processor.name, ok=False, summary="Error", error=str(exc))
            elapsed = (time.perf_counter() - started) * 1000
            results.append(
                ProcessorResult(
                    result.processor,
                    result.ok,
                    result.summary,
                    result.values,
                    result.warnings,
                    result.error,
                    elapsed,
                )
            )
        return results
