from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

AUDIT_LATENCY_BUCKETS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]

deltallm_audit_queue_depth_metric = Gauge(
    "deltallm_audit_queue_depth",
    "Current audit ingestion queue depth",
    registry=get_prometheus_registry(),
)

deltallm_audit_write_failures_metric = Counter(
    "deltallm_audit_write_failures_total",
    "Total failed audit write attempts",
    ["path"],
    registry=get_prometheus_registry(),
)

deltallm_audit_events_dropped_metric = Counter(
    "deltallm_audit_events_dropped_total",
    "Total dropped audit events",
    ["reason"],
    registry=get_prometheus_registry(),
)

deltallm_audit_ingestion_latency_metric = Histogram(
    "deltallm_audit_ingestion_latency_seconds",
    "Audit write latency",
    ["path"],
    buckets=AUDIT_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)


def set_audit_queue_depth(value: int) -> None:
    deltallm_audit_queue_depth_metric.set(max(0, int(value)))


def increment_audit_write_failure(*, path: str) -> None:
    deltallm_audit_write_failures_metric.labels(path=sanitize_label(path)).inc()


def increment_audit_events_dropped(*, reason: str) -> None:
    deltallm_audit_events_dropped_metric.labels(reason=sanitize_label(reason)).inc()


def observe_audit_ingestion_latency(*, path: str, latency_seconds: float) -> None:
    deltallm_audit_ingestion_latency_metric.labels(path=sanitize_label(path)).observe(max(0.0, float(latency_seconds)))
