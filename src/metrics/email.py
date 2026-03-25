from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

EMAIL_LATENCY_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]

deltallm_email_queue_depth_metric = Gauge(
    "deltallm_email_queue_depth",
    "Current email outbox queue depth",
    registry=get_prometheus_registry(),
)

deltallm_email_delivery_attempts_metric = Counter(
    "deltallm_email_delivery_attempts_total",
    "Email delivery attempts by provider, kind, and terminal status",
    ["provider", "kind", "status"],
    registry=get_prometheus_registry(),
)

deltallm_email_delivery_latency_metric = Histogram(
    "deltallm_email_delivery_latency_seconds",
    "Email delivery latency",
    ["provider", "kind"],
    buckets=EMAIL_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)


def set_email_queue_depth(value: int) -> None:
    deltallm_email_queue_depth_metric.set(max(0, int(value)))


def increment_email_delivery_attempt(*, provider: str, kind: str, status: str) -> None:
    deltallm_email_delivery_attempts_metric.labels(
        provider=sanitize_label(provider),
        kind=sanitize_label(kind),
        status=sanitize_label(status),
    ).inc()


def observe_email_delivery_latency(*, provider: str, kind: str, latency_seconds: float) -> None:
    deltallm_email_delivery_latency_metric.labels(
        provider=sanitize_label(provider),
        kind=sanitize_label(kind),
    ).observe(max(0.0, float(latency_seconds)))
