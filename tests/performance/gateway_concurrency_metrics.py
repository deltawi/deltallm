"""Bounded, allowlisted process samples for the local concurrency workload."""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Literal, TextIO, TypeVar

import httpx
from prometheus_client.parser import text_string_to_metric_families
from src.db.telemetry_acceptance import AcceptanceFailure
from src.metrics.request_phases import OUTCOMES, PHASES, RESPONSE_KINDS, ROUTES
from src.metrics.telemetry_acceptance import AcceptancePhase

MAX_METRICS_BYTES = 2 * 1024 * 1024
PROCESS_METRICS_PARSE_MIN_BYTES = 64 * 1024
PROCESS_METRICS_ENCODE_MIN_SAMPLES = 256
MAX_SAMPLES_PER_SCRAPE = 10000
MAX_SNAPSHOTS = 3602
MAX_EXPORT_BYTES = 256 * 1024 * 1024
T = TypeVar("T")
MetricIdentity = tuple[str, tuple[tuple[str, str], ...]]
HISTOGRAMS = (
    "deltallm_telemetry_acceptance_phase_seconds",
    "deltallm_telemetry_acceptance_events_per_commit",
    "deltallm_request_phase_latency_seconds",
    "deltallm_event_loop_lag_seconds",
    "deltallm_ingress_queue_seconds",
    "deltallm_auth_fallback_seconds",
    "deltallm_database_allocation_seconds",
    "deltallm_redis_allocation_acquisition_seconds",
    "deltallm_redis_command_round_trip_seconds",
    "deltallm_redis_pipeline_commands",
    "deltallm_metrics_snapshot_generation_seconds",
    "deltallm_python_gc_pause_seconds",
    "deltallm_accounting_batch_size",
    "deltallm_accounting_batch_seconds",
    "deltallm_accounting_queue_wait_seconds",
    "deltallm_accounting_database_call_seconds",
    "deltallm_accounting_journal_worker_action_seconds",
    "deltallm_bounded_work_seconds",
    "deltallm_readiness_refresh_seconds",
    "deltallm_shutdown_phase_seconds",
    "deltallm_shutdown_cleanup_seconds",
)
ALLOWED_NAMES = {
    "deltallm_request_phase_in_flight",
    "deltallm_metrics_snapshot_generations_total",
    "deltallm_metrics_snapshot_timestamp_seconds",
    "deltallm_metrics_snapshot_bytes",
    "deltallm_redis_allocation_occupied",
    "deltallm_redis_allocation_waiters",
    "deltallm_redis_allocation_events_total",
    "deltallm_redis_command_round_trips_total",
    "deltallm_optional_request_diagnostics_total",
    "deltallm_prompt_cache_lookups_total",
    "deltallm_readiness_probes_total",
    "deltallm_process_state",
    "deltallm_shutdown_cleanup_total",
    "deltallm_shutdown_forced_exit_intent_total",
    "deltallm_request_deadline_expirations_total",
    "deltallm_bounded_work_rejections_total",
    "deltallm_bounded_work_in_flight",
    "deltallm_bounded_work_bytes",
    "deltallm_callback_outcomes_total",
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
    "deltallm_spend_ingestion_failures_total",
    "deltallm_spend_operation_unknown",
    "deltallm_spend_operation_observed_timestamp_seconds",
    "deltallm_spend_operation_transitions_total",
    "deltallm_spend_ingestion_oldest_event_age_seconds",
    "deltallm_spend_ingestion_fallback_active",
    "deltallm_spend_ingestion_fallback_waiters",
    "deltallm_ingress_active",
    "deltallm_ingress_waiters",
    "deltallm_ingress_buffered_bytes",
    "deltallm_ingress_rejections_total",
    "deltallm_auth_fallback_tasks",
    "deltallm_auth_fallback_callers",
    "deltallm_auth_fallback_events_total",
    "deltallm_database_allocation_occupied",
    "deltallm_database_allocation_events_total",
    "deltallm_accounting_queue_depth",
    "deltallm_accounting_failures_total",
    "deltallm_accounting_reservation_decisions_total",
    "deltallm_accounting_projection_actions_total",
    "deltallm_accounting_projection_backlog",
    "deltallm_accounting_projection_oldest_event_age_seconds",
    "deltallm_accounting_queue_retained_bytes",
    "deltallm_accounting_permit_actions_total",
    "deltallm_accounting_permit_bank_subjects",
    "deltallm_accounting_permit_bank_available",
    "deltallm_accounting_permit_bank_retained_bytes",
    "deltallm_accounting_journal_worker_actions_total",
    "deltallm_accounting_read_model_progress_available",
    "deltallm_accounting_read_model_observed_timestamp_seconds",
    "deltallm_accounting_read_model_pending_partitions",
    "deltallm_accounting_read_model_oldest_head_age_seconds",
    "deltallm_accounting_native_oldest_work_age_seconds",
    "deltallm_accounting_native_work_observation_available",
    "deltallm_accounting_native_work_observed_timestamp_seconds",
} | {name + suffix for name in HISTOGRAMS for suffix in ("_bucket", "_count", "_sum")}
LABEL_VALUES = {
    "owner": {
        "authentication",
        "rate_limit",
        "concurrency",
        "routing",
        "cache",
        "mixed",
        "system",
        "unknown",
    },
    "family": {"read", "write", "cleanup", "lua", "pipeline", "other"},
    "entity": {"binding", "prompt", "group_default"},
    "tier": {"l1", "l2", "negative_l1", "negative_l2", "db", "db_miss", "write_error"},
    "generation": {"0", "1", "2"},
    "stage": {
        "operation_admission",
        "operation_receipt",
        "operation_unknown",
        "operation_recovery",
    },
    "state": {
        "starting",
        "serving",
        "draining",
        "stopping",
        "stopped",
        "dispatched",
        "accepted",
        "unknown",
    },
    "component": {
        "redis",
        "database",
        "foreground_database",
        "telemetry_database",
        "telemetry_worker_database",
        "telemetry_settlement_database",
    },
    "queue": {"audit", "spend", "reservation", "finalization"},
    "lane": {str(value) for value in range(8)},
    "phase": PHASES
    | {phase.value for phase in AcceptancePhase}
    | {
        "cache_read",
        "cache_write",
        "lookup",
        "admission",
        "caller",
        "execution",
        "withdrawal",
        "responses",
        "cancellation",
        "workers",
        "close",
        "queue",
        "database",
    },
    "outcome": OUTCOMES
    | {
        "ready",
        "accepted",
        "duplicate",
        "full",
        "mixed",
        "empty",
        "closed",
        "overloaded",
        "deadline",
        "queue_full",
        "queue_timeout",
        "coalesced",
        "completed",
        "failed",
        "miss",
        "hit",
        "unavailable_or_invalid",
        "unavailable",
        "oversized",
        "stored",
        "admitted",
        "rejected",
        "caller_deadline",
        "caller_cancelled",
        "rollback_error",
        "cancelled",
        "timeout",
        "hook_failed",
        "stale",
        "acquired",
        "failure",
        "skipped",
        "error",
    },
    "transaction_scope": {"owned", "external"},
    "route": ROUTES,
    "response_kind": RESPONSE_KINDS,
    "reason": {reason.value for reason in AcceptanceFailure}
    | {
        "gateway_ingress_full",
        "gateway_draining",
        "gateway_ingress_buffer_full",
        "gateway_request_body_too_large",
        "gateway_request_body_timeout",
        "invalid_content_length",
        "closed",
        "full",
        "bytes",
        "payload",
        "close_failed",
        "queue_full",
        "queue_closed",
        "incomplete_result",
        "invalid_result",
        "client_rejection",
        "dependency_unavailable",
        "internal_error",
    },
    "allocation": {
        "critical",
        "cache",
        "bulk",
        "inference",
        "control",
        "health",
        "foreground",
        "telemetry",
        "telemetry_settlement",
        "telemetry_worker",
        "callback",
        "callback_sync",
        "callback_resources",
        "guardrail",
    },
    "operation": {
        "query",
        "finish",
        "admit_grant",
        "ensure_grants",
        "reserve_grant",
        "reserve_direct",
        "recover_reservation",
        "finalize_grant",
        "finalize_direct",
        "recover_finalization",
        "allocate_local_permit_grants",
        "allocate_permit_grants",
        "claim_permits",
        "return_local_permits",
        "append_local_terminal",
        "claim_terminal_journal",
        "materialize_terminal_journal",
        "claim_read_model",
        "read_model_progress",
        "append_terminal_journal",
        "recover_terminal_journal",
        "recover_terminal_claim",
        "recover_terminal_materialization",
        "fail_terminal_journal",
        "terminal_backlog_snapshot",
        "finalize_local_permits",
        "recover_local_permit_grants",
        "recover_local_permit_returns",
        "recover_local_permit_finalizations",
        "recover_permit_grants",
        "recover_permit_claims",
        "initialize_read_model",
        "verify_read_model_cells",
        "recover_read_model_claim",
        "project_read_model",
        "presence_initialize",
        "presence_acquire",
        "presence_publish",
        "presence_release",
        "presence_snapshot",
        "recovery_expired_grants",
        "recovery_expired_operations",
        "recovery_settle_grants",
        "recovery_roll_windows",
    },
    "decision": {"dispatch", "replay", "budget_exhausted", "capacity_exhausted"},
    "action": {
        "recovered",
        "window_rolled",
        "event_projected",
        "iteration",
        "claim",
        "materialize",
        "failure",
        "read_model_commit",
        "read_model_run",
        "refill",
        "issue",
        "return",
        "retire",
        "expired_grants",
        "expired_operations",
        "settle_grants",
        "roll_windows",
        "recovery_tick",
    },
    "response": {"started", "not_started"},
    "integration": {"prometheus", "langfuse", "opentelemetry", "s3", "custom"},
}


@dataclass(frozen=True)
class MetricValue:
    name: str
    labels: dict[str, str]
    value: float


@dataclass(frozen=True)
class MetricSource:
    url: str
    role: Literal["api", "accounting_worker", "accounting_request"]
    process: int

    def __post_init__(self) -> None:
        if self.role not in ("api", "accounting_worker", "accounting_request"):
            raise ValueError("unsupported metrics source role")
        if not 0 <= self.process < 16:
            raise ValueError("metrics source process must be between zero and fifteen")


@dataclass(frozen=True)
class EncodedMetricRecord:
    line: str
    metric_names: frozenset[str]
    values: dict[MetricIdentity, float] | None


def _encode_metric_records(
    results: list[list[MetricValue] | None],
    sources: list[MetricSource],
    offset: float,
) -> tuple[EncodedMetricRecord, ...]:
    records: list[EncodedMetricRecord] = []
    for index, result in enumerate(results):
        source = sources[index]
        if not result:
            record = {
                "offset_seconds": offset,
                "source": index,
                "source_role": source.role,
                "source_process": source.process,
                "error": "scrape_failed",
            }
            names = frozenset[str]()
            values = None
        else:
            record = {
                "offset_seconds": offset,
                "source": index,
                "source_role": source.role,
                "source_process": source.process,
                "samples": [asdict(item) for item in result],
            }
            names = frozenset(item.name for item in result)
            values = {
                (sample.name, tuple(sorted(sample.labels.items()))): sample.value
                for sample in result
            }
        records.append(
            EncodedMetricRecord(
                line=json.dumps(record, sort_keys=True) + "\n",
                metric_names=names,
                values=values,
            )
        )
    return tuple(records)


def _write_snapshot(file: TextIO, text: str) -> None:
    file.write(text)
    file.flush()


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
    def __init__(
        self, sources: list[str | MetricSource], output: Path, *, interval_seconds: float = 1
    ) -> None:
        if not 1 <= len(sources) <= 32:
            raise ValueError("provide one to thirty-two distinct per-process metrics endpoints")
        if not math.isfinite(interval_seconds) or not 0.1 <= interval_seconds <= 60:
            raise ValueError("metrics interval must be between 0.1 and 60 seconds")
        self.interval_seconds = interval_seconds
        self.sources = [
            source
            if isinstance(source, MetricSource)
            else MetricSource(url=source, role="api", process=index)
            for index, source in enumerate(sources)
        ]
        urls = [source.url for source in self.sources]
        if len(set(urls)) != len(urls):
            raise ValueError("metrics endpoints must be distinct")
        self.output = output
        self.started = 0.0
        self.snapshots = 0
        self.errors = 0
        self.successful_scrapes = [0] * len(self.sources)
        self.failed_scrapes = [0] * len(self.sources)
        self.metric_names = [set[str]() for _ in self.sources]
        self._baseline_captured = [False] * len(self.sources)
        self._first_values = [dict[MetricIdentity, float]() for _ in self.sources]
        self._last_values = [dict[MetricIdentity, float]() for _ in self.sources]
        self._bytes_written = 0
        self._file: TextIO | None = None
        self._client: httpx.AsyncClient | None = None
        self._cpu_executor: ProcessPoolExecutor | None = None
        self._io_executor: ThreadPoolExecutor | None = None
        self._task: asyncio.Task[None] | None = None
        self._workload: asyncio.Task[object] | None = None
        self._stop = asyncio.Event()

    async def __aenter__(self) -> MetricsRecorder:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.output.open("x", encoding="utf-8")
        try:
            self._cpu_executor = ProcessPoolExecutor(max_workers=1)
            self._io_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="concurrency-metrics-io"
            )
            self._client = httpx.AsyncClient(
                timeout=1.0,
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=0),
                follow_redirects=False,
                trust_env=False,
            )
            self.started = perf_counter()
            await self.snapshot(buckets=True)
            self._task = asyncio.create_task(self._run(), name="concurrency-metrics")
        except BaseException:
            await self._close()
            raise
        return self

    async def run_workload(self, operation: Callable[[], Awaitable[T]]) -> T:
        if (
            self._task is None
            or self._task.done()
            or self._stop.is_set()
            or self._workload is not None
        ):
            raise RuntimeError("One workload requires one running metrics recorder")

        async def invoke() -> T:
            return await operation()

        task = asyncio.create_task(invoke(), name="concurrency-workload")
        self._workload = task
        try:
            done, _ = await asyncio.wait((task, self._task), return_when=asyncio.FIRST_COMPLETED)
            if self._task in done:
                self._task.result()
                raise RuntimeError("metrics collector stopped during workload")
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._workload = None

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
        assert self._cpu_executor is not None and self._io_executor is not None
        executor = (
            self._cpu_executor
            if len(body) >= PROCESS_METRICS_PARSE_MIN_BYTES
            else self._io_executor
        )
        return await asyncio.get_running_loop().run_in_executor(
            executor, partial(select_metrics, body.decode("utf-8"), buckets=buckets)
        )

    async def snapshot(self, *, buckets: bool = False) -> None:
        assert self._file is not None
        assert self._cpu_executor is not None and self._io_executor is not None
        if self.snapshots >= MAX_SNAPSHOTS:
            raise ValueError("metrics snapshot budget exceeded")
        results = await asyncio.gather(
            *(self._read(source.url, buckets=buckets) for source in self.sources),
            return_exceptions=True,
        )
        safe_results = [None if isinstance(result, BaseException) else result for result in results]
        sample_count = sum(len(result) for result in safe_results if result is not None)
        executor = (
            self._cpu_executor
            if sample_count >= PROCESS_METRICS_ENCODE_MIN_SAMPLES
            else self._io_executor
        )
        encoded = await asyncio.get_running_loop().run_in_executor(
            executor,
            _encode_metric_records,
            safe_results,
            self.sources,
            perf_counter() - self.started,
        )
        lines: list[str] = []
        for index, record in enumerate(encoded):
            if record.values is None:
                self.errors += 1
                self.failed_scrapes[index] += 1
            else:
                self.successful_scrapes[index] += 1
                self.metric_names[index].update(record.metric_names)
                self._capture_values(index, record.values)
            self._bytes_written += len(record.line.encode("utf-8"))
            if self._bytes_written > MAX_EXPORT_BYTES:
                raise ValueError("metrics export byte budget exceeded")
            lines.append(record.line)
        await asyncio.get_running_loop().run_in_executor(
            self._io_executor, _write_snapshot, self._file, "".join(lines)
        )
        self.snapshots += 1

    def evidence(self) -> list[dict[str, object]]:
        return [
            {
                "source": index,
                "source_role": source.role,
                "source_process": source.process,
                "successful_scrapes": self.successful_scrapes[index],
                "failed_scrapes": self.failed_scrapes[index],
                "accounting_metrics_observed": any(
                    name.startswith("deltallm_accounting_") for name in self.metric_names[index]
                ),
            }
            for index, source in enumerate(self.sources)
        ]

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                await self.snapshot()

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
            await self.snapshot(buckets=True)
        finally:
            await self._close()

    async def _close(self) -> None:
        try:
            if self._client is not None:
                async with asyncio.timeout(2):
                    await self._client.aclose()
        finally:
            try:
                if self._cpu_executor is not None:
                    self._cpu_executor.shutdown(wait=True, cancel_futures=True)
            finally:
                try:
                    if self._io_executor is not None:
                        self._io_executor.shutdown(wait=True, cancel_futures=True)
                finally:
                    if self._file is not None:
                        self._file.close()

    def _capture_values(self, source: int, values: dict[MetricIdentity, float]) -> None:
        if not self._baseline_captured[source]:
            self._first_values[source] = values.copy()
            self._baseline_captured[source] = True
        # A bounded scrape may omit a previously observed series when a label has
        # no current samples. Cumulative counters do not become zero in that case:
        # retain their last observation so the workload delta remains causal.
        if len(self._last_values[source].keys() | values.keys()) > MAX_SAMPLES_PER_SCRAPE:
            raise ValueError("retained metric identity budget exceeded")
        self._last_values[source].update(values)

    def counter_delta(
        self,
        name: str,
        *,
        labels: dict[str, str] | None = None,
        roles: frozenset[str] = frozenset({"api"}),
    ) -> float:
        """Return one same-window counter delta across the selected process roles."""

        return sum(
            item["delta"]
            for item in self.counter_deltas_by_source(name, labels=labels, roles=roles)
        )

    def counter_deltas_by_source(
        self,
        name: str,
        *,
        labels: dict[str, str] | None = None,
        roles: frozenset[str] = frozenset({"api"}),
    ) -> list[dict[str, object]]:
        """Return same-window counter deltas without exposing endpoint identity."""

        required = labels or {}
        result: list[dict[str, object]] = []
        for index, source in enumerate(self.sources):
            if source.role not in roles or not self._baseline_captured[index]:
                continue
            first, last = self._first_values[index], self._last_values[index]
            identities = {
                identity
                for identity in first.keys() | last.keys()
                if identity[0] == name
                and all(dict(identity[1]).get(key) == value for key, value in required.items())
            }
            source_delta = 0.0
            for identity in identities:
                identity_delta = last.get(identity, 0.0) - first.get(identity, 0.0)
                if identity_delta < 0:
                    raise ValueError("metrics counter decreased during workload")
                source_delta += identity_delta
            result.append(
                {
                    "source_role": source.role,
                    "source_process": source.process,
                    "delta": source_delta,
                }
            )
        return result

    def histogram_delta(
        self,
        name: str,
        *,
        labels: dict[str, str] | None = None,
        roles: frozenset[str] = frozenset({"api"}),
    ) -> dict[str, float | None]:
        """Return a bounded same-window histogram summary across process roles."""

        if name not in HISTOGRAMS:
            raise ValueError("histogram is not allowlisted")
        required = labels or {}
        count = 0.0
        total = 0.0
        buckets: dict[float, float] = {}
        for index, source in enumerate(self.sources):
            if source.role not in roles or not self._baseline_captured[index]:
                continue
            first, last = self._first_values[index], self._last_values[index]
            for identity in first.keys() | last.keys():
                metric_name, identity_labels = identity
                if metric_name not in {
                    name + "_count",
                    name + "_sum",
                    name + "_bucket",
                }:
                    continue
                label_values = dict(identity_labels)
                if not all(label_values.get(key) == value for key, value in required.items()):
                    continue
                delta = last.get(identity, 0.0) - first.get(identity, 0.0)
                if delta < 0:
                    raise ValueError("metrics counter decreased during workload")
                if metric_name == name + "_count":
                    count += delta
                elif metric_name == name + "_sum":
                    total += delta
                elif metric_name == name + "_bucket" and "le" in label_values:
                    upper_bound = float(label_values["le"])
                    buckets[upper_bound] = buckets.get(upper_bound, 0.0) + delta

        def percentile(value: float) -> float | None:
            if count <= 0:
                return None
            threshold = count * value
            for upper_bound, cumulative in sorted(buckets.items()):
                if cumulative >= threshold:
                    return upper_bound
            return None

        return {
            "count": count,
            "mean": total / count if count else None,
            "p50_upper_bound": percentile(0.50),
            "p95_upper_bound": percentile(0.95),
            "p99_upper_bound": percentile(0.99),
        }
