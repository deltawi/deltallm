from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

SPEND_INGESTION_BUCKETS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60]

deltallm_spend_ingestion_backlog_metric = Gauge(
    "deltallm_spend_ingestion_backlog",
    "Durable spend ingestion events awaiting completion",
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_oldest_event_age_metric = Gauge(
    "deltallm_spend_ingestion_oldest_event_age_seconds",
    "Age of the oldest active spend ingestion event",
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_capacity_utilization_metric = Gauge(
    "deltallm_spend_ingestion_capacity_utilization",
    "Fraction of configured spend-ingestion pending capacity in use",
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_fallback_active_metric = Gauge(
    "deltallm_spend_ingestion_fallback_active",
    "Synchronous spend fallback operations currently executing",
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_fallback_waiters_metric = Gauge(
    "deltallm_spend_ingestion_fallback_waiters",
    "Synchronous spend fallback operations waiting for execution capacity",
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_enqueue_metric = Counter(
    "deltallm_spend_ingestion_enqueue_total",
    "Spend ingestion enqueue outcomes",
    ["outcome"],
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_cleanup_metric = Counter(
    "deltallm_spend_ingestion_cleanup_deleted_total",
    "Terminal spend-ingestion rows removed by retention cleanup",
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_ledger_rows_metric = Histogram(
    "deltallm_spend_ingestion_ledger_rows",
    "Unique ledger rows updated per committed spend batch",
    ["entity_type"],
    buckets=[0, 1, 5, 10, 25, 50, 100, 250, 500],
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_failures_metric = Counter(
    "deltallm_spend_ingestion_failures_total",
    "Spend ingestion processing failures",
    ["stage"],
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_batch_size_metric = Histogram(
    "deltallm_spend_ingestion_batch_size",
    "Claimed spend ingestion batch size",
    buckets=[1, 5, 10, 25, 50, 100, 250, 500],
    registry=get_prometheus_registry(),
)
deltallm_spend_ingestion_latency_metric = Histogram(
    "deltallm_spend_ingestion_processing_seconds",
    "Spend ingestion batch processing latency",
    buckets=SPEND_INGESTION_BUCKETS,
    registry=get_prometheus_registry(),
)


def set_spend_ingestion_backlog(value: int) -> None:
    deltallm_spend_ingestion_backlog_metric.set(max(0, int(value)))


def set_spend_ingestion_oldest_event_age(value: float) -> None:
    deltallm_spend_ingestion_oldest_event_age_metric.set(max(0.0, float(value)))


def set_spend_ingestion_capacity_utilization(*, pending: int, capacity: int) -> None:
    denominator = max(1, int(capacity))
    deltallm_spend_ingestion_capacity_utilization_metric.set(
        max(0.0, min(1.0, int(pending) / denominator))
    )


def set_spend_ingestion_fallback_active(value: int) -> None:
    deltallm_spend_ingestion_fallback_active_metric.set(max(0, int(value)))


def set_spend_ingestion_fallback_waiters(value: int) -> None:
    deltallm_spend_ingestion_fallback_waiters_metric.set(max(0, int(value)))


def increment_spend_ingestion_enqueue(outcome: str) -> None:
    deltallm_spend_ingestion_enqueue_metric.labels(outcome=sanitize_label(outcome)).inc()


def increment_spend_ingestion_cleanup(value: int) -> None:
    if value > 0:
        deltallm_spend_ingestion_cleanup_metric.inc(int(value))


def observe_spend_ingestion_ledger_rows(*, entity_type: str, value: int) -> None:
    deltallm_spend_ingestion_ledger_rows_metric.labels(
        entity_type=sanitize_label(entity_type)
    ).observe(max(0, int(value)))


def increment_spend_ingestion_failure(stage: str) -> None:
    deltallm_spend_ingestion_failures_metric.labels(stage=sanitize_label(stage)).inc()


def observe_spend_ingestion_batch(value: int) -> None:
    deltallm_spend_ingestion_batch_size_metric.observe(max(0, int(value)))


def observe_spend_ingestion_latency(value: float) -> None:
    deltallm_spend_ingestion_latency_metric.observe(max(0.0, float(value)))
