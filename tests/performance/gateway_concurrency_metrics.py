"""Bounded, allowlisted process samples for the local concurrency workload."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from time import perf_counter
from typing import TextIO

import httpx
from prometheus_client.parser import text_string_to_metric_families
from src.db.telemetry_acceptance import AcceptanceFailure
from src.metrics.request_phases import OUTCOMES, PHASES, RESPONSE_KINDS, ROUTES
from src.metrics.telemetry_acceptance import AcceptancePhase

MAX_METRICS_BYTES = 2 * 1024 * 1024
MAX_SAMPLES_PER_SCRAPE = 10000
MAX_SNAPSHOTS = 3602
MAX_EXPORT_BYTES = 256 * 1024 * 1024
HISTOGRAMS = (
    "deltallm_telemetry_acceptance_phase_seconds",
    "deltallm_telemetry_acceptance_events_per_commit",
    "deltallm_request_phase_latency_seconds",
    "deltallm_event_loop_lag_seconds",
)
ALLOWED_NAMES = {
    "deltallm_telemetry_acceptance_in_flight",
    "deltallm_telemetry_acceptance_operations_total",
    "deltallm_telemetry_acceptance_failures_total",
    "deltallm_telemetry_acceptance_serialized_bytes",
    "deltallm_http_requests_in_flight",
    "deltallm_http_request_body_bytes_total",
    "deltallm_http_response_body_bytes_total",
    "deltallm_event_loop_last_lag_seconds",
    "deltallm_event_loop_samplers",
    "deltallm_audit_queue_depth",
    "deltallm_audit_oldest_event_age_seconds",
    "deltallm_spend_ingestion_backlog",
    "deltallm_spend_ingestion_oldest_event_age_seconds",
    "deltallm_spend_ingestion_fallback_active",
    "deltallm_spend_ingestion_fallback_waiters",
} | {name + suffix for name in HISTOGRAMS for suffix in ("_bucket", "_count", "_sum")}
LABEL_VALUES = {
    "queue": {"audit", "spend"},
    "phase": PHASES | {phase.value for phase in AcceptancePhase},
    "outcome": OUTCOMES | {"accepted", "duplicate", "full", "mixed", "empty"},
    "transaction_scope": {"owned", "external"},
    "route": ROUTES,
    "response_kind": RESPONSE_KINDS,
    "reason": {reason.value for reason in AcceptanceFailure},
}


@dataclass(frozen=True)
class MetricValue:
    name: str
    labels: dict[str, str]
    value: float


def select_metrics(text: str, *, buckets: bool = True) -> list[MetricValue]:
    selected: list[MetricValue] = []
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name not in ALLOWED_NAMES:
                continue
            if sample.name.endswith("_bucket") and not buckets:
                continue
            if not _safe_labels(sample.labels) or not math.isfinite(sample.value):
                continue
            selected.append(MetricValue(sample.name, dict(sample.labels), sample.value))
            if len(selected) > MAX_SAMPLES_PER_SCRAPE:
                raise ValueError("metrics sample budget exceeded")
    return selected


def _safe_labels(labels: dict[str, str]) -> bool:
    for key, value in labels.items():
        if len(value) > 64:
            return False
        if key == "le":
            try:
                if value != "+Inf" and not 0 <= float(value) <= 3600:
                    return False
            except ValueError:
                return False
        elif value not in LABEL_VALUES.get(key, ()):
            return False
    return True


class MetricsRecorder:
    def __init__(self, urls: list[str], output: Path) -> None:
        if not 1 <= len(urls) <= 16:
            raise ValueError("provide one to sixteen distinct per-process metrics endpoints")
        if len(set(urls)) != len(urls):
            raise ValueError("metrics endpoints must be distinct")
        self.urls = urls
        self.output = output
        self.started = 0.0
        self.snapshots = 0
        self.errors = 0
        self._bytes_written = 0
        self._file: TextIO | None = None
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def __aenter__(self) -> MetricsRecorder:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.output.open("x", encoding="utf-8")
        self._client = httpx.AsyncClient(
            timeout=1.0,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=16),
            follow_redirects=False,
            trust_env=False,
        )
        self.started = perf_counter()
        try:
            await self.snapshot(buckets=True)
            self._task = asyncio.create_task(self._run(), name="concurrency-metrics")
        except BaseException:
            await self._client.aclose()
            self._file.close()
            raise
        return self

    async def _read(self, url: str, *, buckets: bool) -> list[MetricValue]:
        assert self._client is not None
        body = bytearray()
        async with asyncio.timeout(1.5):
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_METRICS_BYTES:
                        raise ValueError("metrics byte budget exceeded")
                    body.extend(chunk)
        return select_metrics(body.decode("utf-8"), buckets=buckets)

    async def snapshot(self, *, buckets: bool = False) -> None:
        assert self._file is not None
        if self.snapshots >= MAX_SNAPSHOTS:
            raise ValueError("metrics snapshot budget exceeded")
        results = await asyncio.gather(
            *(self._read(url, buckets=buckets) for url in self.urls), return_exceptions=True
        )
        offset = perf_counter() - self.started
        for index, result in enumerate(results):
            if isinstance(result, BaseException) or not result:
                self.errors += 1
                record = {"offset_seconds": offset, "source": index, "error": "scrape_failed"}
            else:
                record = {
                    "offset_seconds": offset,
                    "source": index,
                    "samples": [asdict(item) for item in result],
                }
            line = json.dumps(record, sort_keys=True) + "\n"
            self._bytes_written += len(line.encode("utf-8"))
            if self._bytes_written > MAX_EXPORT_BYTES:
                raise ValueError("metrics export byte budget exceeded")
            self._file.write(line)
        self._file.flush()
        self.snapshots += 1

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except TimeoutError:
                await self.snapshot()

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
            await self.snapshot(buckets=True)
        finally:
            if self._client is not None:
                await self._client.aclose()
            if self._file is not None:
                self._file.close()
