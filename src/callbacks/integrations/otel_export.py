"""Owned, bounded OpenTelemetry span export.

The SDK batch processor drops spans when its queue is full but does not
report how many. This processor keeps the same shape (one bounded queue, one
export thread, size- or time-triggered batches) and counts every span it
exports, drops, or fails to export.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from enum import Enum
from typing import Any

from src.metrics.request_work import otel_span_outcomes, otel_span_queue_depth

try:
    from opentelemetry.sdk.trace import SpanProcessor
    from opentelemetry.sdk.trace.export import SpanExportResult
except ImportError:  # The callback refuses to start without the SDK.
    SpanProcessor = object  # type: ignore[assignment,misc]

    class SpanExportResult(Enum):  # type: ignore[no-redef]
        SUCCESS = 0
        FAILURE = 1


logger = logging.getLogger(__name__)


class BoundedBatchSpanProcessor(SpanProcessor):  # type: ignore[misc,valid-type]
    def __init__(
        self,
        exporter: Any,
        *,
        max_queue_size: int = 2048,
        max_export_batch_size: int = 512,
        schedule_delay_seconds: float = 1.0,
        shutdown_timeout_seconds: float = 2.0,
    ) -> None:
        if max_queue_size < 1 or not 1 <= max_export_batch_size <= max_queue_size:
            raise ValueError("span export batch size must be between 1 and the queue size")
        self._exporter = exporter
        self._max_queue_size = max_queue_size
        self._batch_size = max_export_batch_size
        self._delay = schedule_delay_seconds
        self._shutdown_timeout = shutdown_timeout_seconds
        self._queue: deque[Any] = deque()
        self._lock = threading.Lock()
        self._export_lock = threading.Lock()
        self._wake = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="deltallm-otel-export", daemon=True)
        self._thread.start()

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        del span, parent_context

    def on_end(self, span: Any) -> None:
        if not span.context.trace_flags.sampled:
            return
        with self._lock:
            if self._stopped or len(self._queue) >= self._max_queue_size:
                otel_span_outcomes.labels("dropped").inc()
                return
            self._queue.append(span)
            depth = len(self._queue)
        otel_span_queue_depth.set(depth)
        if depth >= self._batch_size:
            self._wake.set()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        self._drain()
        return True

    def shutdown(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        self._wake.set()
        self._thread.join(self._shutdown_timeout)
        # Spans still queued after the drain deadline are counted, not exported.
        with self._lock:
            remaining = len(self._queue)
            self._queue.clear()
        if remaining:
            otel_span_outcomes.labels("dropped").inc(remaining)
        otel_span_queue_depth.set(0)
        self._exporter.shutdown()

    def _run(self) -> None:
        while True:
            self._wake.wait(self._delay)
            self._wake.clear()
            self._drain()
            with self._lock:
                if self._stopped:
                    return

    def _drain(self) -> None:
        with self._export_lock:
            while True:
                with self._lock:
                    count = min(self._batch_size, len(self._queue))
                    batch = [self._queue.popleft() for _ in range(count)]
                    depth = len(self._queue)
                if not batch:
                    return
                otel_span_queue_depth.set(depth)
                try:
                    result = self._exporter.export(batch)
                except Exception:
                    logger.warning("opentelemetry span export failed", exc_info=True)
                    result = None
                outcome = "exported" if result == SpanExportResult.SUCCESS else "export_failed"
                otel_span_outcomes.labels(outcome).inc(len(batch))
