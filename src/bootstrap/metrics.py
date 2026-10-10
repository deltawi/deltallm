"""Own bounded process metrics work; never query dependencies from a scrape."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future as ConcurrentFuture, ThreadPoolExecutor
from dataclasses import dataclass
import gc
import logging
from time import perf_counter, time

from fastapi import FastAPI
from starlette.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import CollectorRegistry, generate_latest

from src.metrics.prometheus import get_prometheus_registry
from src.metrics.runtime import (
    event_loop_lag,
    event_loop_last_lag,
    event_loop_samplers,
    metrics_snapshot_bytes,
    metrics_snapshot_generation_seconds,
    metrics_snapshot_generations,
    metrics_snapshot_timestamp,
    python_gc_pause_seconds,
)

SAMPLE_INTERVAL_SECONDS = 1.0
PROMETHEUS_SNAPSHOT_INTERVAL_SECONDS = 5.0
PROMETHEUS_SNAPSHOT_STARTUP_TIMEOUT_SECONDS = 10.0
PROMETHEUS_SNAPSHOT_EXECUTION_TIMEOUT_SECONDS = 5.0
PROMETHEUS_SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024

logger = logging.getLogger(__name__)


class MetricsSnapshotUnavailable(RuntimeError):
    """The process has not completed its first bounded metrics snapshot."""


class MetricsSnapshotGenerationTimedOut(RuntimeError):
    """The single metrics encoder exceeded its owned execution deadline."""


class MetricsSnapshotTooLarge(RuntimeError):
    """The encoded registry exceeded the retained snapshot budget."""


@dataclass(frozen=True, slots=True)
class PrometheusSnapshot:
    content: bytes
    generated_at: float


class PrometheusSnapshotService:
    """Serialize one immutable registry snapshot at a time outside the event loop."""

    def __init__(
        self,
        *,
        registry: CollectorRegistry | None = None,
        encoder: Callable[[CollectorRegistry], bytes] = generate_latest,
        interval_seconds: float = PROMETHEUS_SNAPSHOT_INTERVAL_SECONDS,
        execution_timeout_seconds: float = PROMETHEUS_SNAPSHOT_EXECUTION_TIMEOUT_SECONDS,
        max_snapshot_bytes: int = PROMETHEUS_SNAPSHOT_MAX_BYTES,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("metrics snapshot interval must be positive")
        if execution_timeout_seconds <= 0:
            raise ValueError("metrics snapshot execution timeout must be positive")
        if max_snapshot_bytes <= 0:
            raise ValueError("metrics snapshot byte budget must be positive")
        self._registry = registry if registry is not None else get_prometheus_registry()
        self._encoder = encoder
        self._interval_seconds = interval_seconds
        self._execution_timeout_seconds = execution_timeout_seconds
        self._max_snapshot_bytes = max_snapshot_bytes
        self._executor: ThreadPoolExecutor | None = None
        self._refresh_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._snapshot: PrometheusSnapshot | None = None
        self._generation: ConcurrentFuture[bytes] | None = None
        self._discard_generation = False

    @property
    def snapshot(self) -> PrometheusSnapshot:
        snapshot = self._snapshot
        if snapshot is None:
            raise MetricsSnapshotUnavailable("metrics snapshot is not ready")
        return snapshot

    async def start(self, *, periodic: bool = True) -> None:
        if self._executor is not None:
            raise RuntimeError("metrics snapshot service is already started")
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="metrics-snapshot")
        try:
            async with asyncio.timeout(PROMETHEUS_SNAPSHOT_STARTUP_TIMEOUT_SECONDS):
                await self.refresh()
        except BaseException:
            generation = self._generation
            if generation is not None:
                generation.cancel()
            self._generation = None
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
            raise
        if periodic:
            self._task = asyncio.create_task(self._run(), name="prometheus-snapshot")

    async def refresh(self) -> PrometheusSnapshot:
        executor = self._executor
        if executor is None:
            raise RuntimeError("metrics snapshot service is not started")
        async with self._refresh_lock:
            if self._generation is not None:
                if not self._generation.done():
                    metrics_snapshot_generations.labels("skipped").inc()
                    return self.snapshot
                self._discard_completed_generation()

            candidate_timestamp = time()
            previous_timestamp = self._snapshot.generated_at if self._snapshot is not None else 0.0
            metrics_snapshot_timestamp.set(candidate_timestamp)
            started = perf_counter()
            generation = executor.submit(self._encoder, self._registry)
            self._generation = generation
            self._discard_generation = False
            wrapped = asyncio.wrap_future(generation)
            try:
                done, _pending = await asyncio.wait(
                    {wrapped}, timeout=self._execution_timeout_seconds
                )
                if not done:
                    self._discard_generation = True
                    metrics_snapshot_timestamp.set(previous_timestamp)
                    metrics_snapshot_generations.labels("failure").inc()
                    raise MetricsSnapshotGenerationTimedOut(
                        "metrics snapshot generation deadline exceeded"
                    )
                content = wrapped.result()
                if len(content) > self._max_snapshot_bytes:
                    raise MetricsSnapshotTooLarge(
                        "metrics snapshot exceeded the retained byte budget"
                    )
            except BaseException:
                if not self._discard_generation:
                    metrics_snapshot_timestamp.set(previous_timestamp)
                    metrics_snapshot_generations.labels("failure").inc()
                raise
            finally:
                if not wrapped.done():
                    wrapped.cancel()
                if generation.done():
                    self._generation = None
                    self._discard_generation = False
            duration = perf_counter() - started
            snapshot = PrometheusSnapshot(content=content, generated_at=candidate_timestamp)
            self._snapshot = snapshot
            metrics_snapshot_generation_seconds.observe(duration)
            metrics_snapshot_generations.labels("success").inc()
            missed_intervals = int(duration // self._interval_seconds)
            if missed_intervals:
                metrics_snapshot_generations.labels("skipped").inc(missed_intervals)
            metrics_snapshot_bytes.set(len(content))
            return snapshot

    def _discard_completed_generation(self) -> None:
        generation = self._generation
        if generation is None or not generation.done():
            return
        try:
            generation.exception()
        finally:
            self._generation = None
            self._discard_generation = False

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                try:
                    await self.refresh()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Prometheus snapshot generation failed; serving last snapshot")

    async def close(self) -> None:
        self._stop.set()
        task = self._task
        try:
            if task is not None:
                await task
        except asyncio.CancelledError:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            self._task = None
            generation = self._generation
            if generation is not None:
                generation.cancel()
            self._generation = None
            executor = self._executor
            self._executor = None
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)


def metrics_snapshot_response(service: PrometheusSnapshotService | None) -> Response:
    if not isinstance(service, PrometheusSnapshotService):
        return Response(status_code=503, content="metrics snapshot service unavailable\n")
    try:
        content = service.snapshot.content
    except MetricsSnapshotUnavailable:
        return Response(status_code=503, content="metrics snapshot not ready\n")
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)


async def start_prometheus_snapshots(app: FastAPI) -> PrometheusSnapshotService:
    service = PrometheusSnapshotService()
    await service.start()
    app.state.prometheus_snapshot_service = service
    return service


class RuntimeMetricSampler:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._timer: asyncio.TimerHandle | None = None
        self._due = 0.0
        self._running = False
        self._gc_started: dict[int, float] = {}

    def start(self) -> None:
        if self._running:
            return
        self._schedule()
        self._running = True
        gc.callbacks.append(self._observe_gc)
        event_loop_samplers.inc()

    def _observe_gc(self, phase: str, info: dict[str, int]) -> None:
        if not self._running:
            return
        generation = int(info.get("generation", -1))
        if generation not in (0, 1, 2):
            return
        if phase == "start":
            self._gc_started[generation] = perf_counter()
            return
        if phase != "stop":
            return
        started = self._gc_started.pop(generation, None)
        if started is not None:
            python_gc_pause_seconds.labels(str(generation)).observe(
                max(0.0, perf_counter() - started)
            )

    def _schedule(self) -> None:
        self._due = self._loop.time() + SAMPLE_INTERVAL_SECONDS
        self._timer = self._loop.call_later(SAMPLE_INTERVAL_SECONDS, self._sample)

    def _sample(self) -> None:
        if not self._running:
            return
        lag = max(0.0, self._loop.time() - self._due)
        try:
            event_loop_lag.observe(lag)
            event_loop_last_lag.set(lag)
        finally:
            if self._running:
                self._schedule()

    def close(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            gc.callbacks.remove(self._observe_gc)
        except ValueError:
            pass
        self._gc_started.clear()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        event_loop_samplers.dec()


def start_runtime_metrics() -> RuntimeMetricSampler:
    sampler = RuntimeMetricSampler(asyncio.get_running_loop())
    sampler.start()
    return sampler


def freeze_startup_heap() -> int:
    """Exclude the stable startup object graph from later full-GC scans."""

    gc.collect(2)
    gc.freeze()
    frozen = gc.get_freeze_count()
    logger.info("froze %s startup objects outside the serving window", frozen)
    return frozen
