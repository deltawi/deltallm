from __future__ import annotations

from prometheus_client import Counter

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

deltallm_notification_enqueue_metric = Counter(
    "deltallm_notification_enqueue_total",
    "Notification enqueue attempts by kind and status",
    ["kind", "status"],
    registry=get_prometheus_registry(),
)


def increment_notification_enqueue(*, kind: str, status: str) -> None:
    deltallm_notification_enqueue_metric.labels(
        kind=sanitize_label(kind),
        status=sanitize_label(status),
    ).inc()
