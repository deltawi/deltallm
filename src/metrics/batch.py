from __future__ import annotations

from typing import Any, Mapping

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

BATCH_LATENCY_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]

deltallm_batch_jobs_metric = Gauge(
    "deltallm_batch_jobs",
    "Current batch jobs by status",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_items_metric = Gauge(
    "deltallm_batch_items",
    "Current batch items by status",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_oldest_item_age_metric = Gauge(
    "deltallm_batch_oldest_item_age_seconds",
    "Age in seconds of the oldest batch item by active status",
    ["status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_worker_saturation_metric = Gauge(
    "deltallm_batch_worker_saturation_ratio",
    "Current active worker concurrency / configured concurrency",
    ["worker_id"],
    registry=get_prometheus_registry(),
)

deltallm_batch_finalization_retries_metric = Counter(
    "deltallm_batch_finalization_retries_total",
    "Batch finalization retries by result",
    ["result"],
    registry=get_prometheus_registry(),
)

deltallm_batch_artifact_failures_metric = Counter(
    "deltallm_batch_artifact_failures_total",
    "Batch artifact operation failures by operation and backend",
    ["operation", "backend"],
    registry=get_prometheus_registry(),
)

deltallm_batch_repair_actions_metric = Counter(
    "deltallm_batch_repair_actions_total",
    "Batch repair actions by action and status",
    ["action", "status"],
    registry=get_prometheus_registry(),
)

deltallm_batch_item_reclaims_metric = Counter(
    "deltallm_batch_item_reclaims_total",
    "Batch item reclaims caused by expired in-progress leases",
    registry=get_prometheus_registry(),
)

deltallm_batch_create_latency_metric = Histogram(
    "deltallm_batch_create_latency_seconds",
    "Batch create latency",
    ["status"],
    buckets=BATCH_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_batch_finalize_latency_metric = Histogram(
    "deltallm_batch_finalize_latency_seconds",
    "Batch finalization latency",
    ["status"],
    buckets=BATCH_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_batch_item_execution_latency_metric = Histogram(
    "deltallm_batch_item_execution_latency_seconds",
    "Individual batch item execution latency",
    ["status"],
    buckets=BATCH_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)


def set_batch_job_count(*, status: str, count: int) -> None:
    deltallm_batch_jobs_metric.labels(status=sanitize_label(status)).set(max(0, int(count)))


def set_batch_item_count(*, status: str, count: int) -> None:
    deltallm_batch_items_metric.labels(status=sanitize_label(status)).set(max(0, int(count)))


def set_batch_oldest_item_age(*, status: str, age_seconds: float) -> None:
    deltallm_batch_oldest_item_age_metric.labels(status=sanitize_label(status)).set(max(0.0, float(age_seconds)))


def set_batch_worker_saturation(*, worker_id: str, active: int, capacity: int) -> None:
    denominator = max(1, int(capacity))
    deltallm_batch_worker_saturation_metric.labels(worker_id=sanitize_label(worker_id)).set(
        max(0.0, float(active) / float(denominator))
    )


def increment_batch_finalization_retry(*, result: str) -> None:
    deltallm_batch_finalization_retries_metric.labels(result=sanitize_label(result)).inc()


def increment_batch_artifact_failure(*, operation: str, backend: str) -> None:
    deltallm_batch_artifact_failures_metric.labels(
        operation=sanitize_label(operation),
        backend=sanitize_label(backend),
    ).inc()


def increment_batch_repair_action(*, action: str, status: str) -> None:
    deltallm_batch_repair_actions_metric.labels(
        action=sanitize_label(action),
        status=sanitize_label(status),
    ).inc()


def increment_batch_item_reclaim() -> None:
    deltallm_batch_item_reclaims_metric.inc()


def observe_batch_create_latency(*, status: str, latency_seconds: float) -> None:
    deltallm_batch_create_latency_metric.labels(status=sanitize_label(status)).observe(max(0.0, float(latency_seconds)))


def observe_batch_finalize_latency(*, status: str, latency_seconds: float) -> None:
    deltallm_batch_finalize_latency_metric.labels(status=sanitize_label(status)).observe(max(0.0, float(latency_seconds)))


def observe_batch_item_execution_latency(*, status: str, latency_seconds: float) -> None:
    deltallm_batch_item_execution_latency_metric.labels(status=sanitize_label(status)).observe(max(0.0, float(latency_seconds)))


def publish_batch_runtime_summary(summary: Mapping[str, Any]) -> None:
    """Publish gauges from a summarize_runtime_statuses() result dict.

    Centralizes the mapping so new gauges only need to be added in one place.
    Note: deltallm_batch_jobs only exposes queued/in_progress/finalizing. For
    completed/failed/cancelled throughput, use per-item counters or the
    execution latency histogram's _count series.
    """
    set_batch_job_count(status="queued", count=int(summary.get("queued", 0)))
    set_batch_job_count(status="in_progress", count=int(summary.get("in_progress", 0)))
    set_batch_job_count(status="finalizing", count=int(summary.get("finalizing", 0)))
    set_batch_item_count(status="pending", count=int(summary.get("pending_items", 0)))
    set_batch_item_count(status="in_progress", count=int(summary.get("in_progress_items", 0)))
    set_batch_oldest_item_age(
        status="pending",
        age_seconds=float(summary.get("oldest_pending_item_age_seconds", 0.0)),
    )
    set_batch_oldest_item_age(
        status="in_progress",
        age_seconds=float(summary.get("oldest_in_progress_item_age_seconds", 0.0)),
    )
