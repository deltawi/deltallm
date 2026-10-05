from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry

ACCOUNTING_LATENCY_BUCKETS = [
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2,
]

accounting_queue_depth = Gauge(
    "deltallm_accounting_queue_depth",
    "Accounting requests waiting behind the active microbatch",
    ["queue"],
    registry=get_prometheus_registry(),
)
accounting_batch_size = Histogram(
    "deltallm_accounting_batch_size",
    "Durable accounting operations per PostgreSQL call",
    ["queue"],
    buckets=[1, 2, 4, 8, 16, 32, 64, 128, 256],
    registry=get_prometheus_registry(),
)
accounting_queue_retained_bytes = Gauge(
    "deltallm_accounting_queue_retained_bytes",
    "Conservative byte charge for queued and selected accounting payloads",
    ["queue"],
    registry=get_prometheus_registry(),
)
accounting_batch_seconds = Histogram(
    "deltallm_accounting_batch_seconds",
    "Accounting PostgreSQL batch acknowledgement latency",
    ["queue", "outcome"],
    buckets=ACCOUNTING_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)
accounting_queue_wait_seconds = Histogram(
    "deltallm_accounting_queue_wait_seconds",
    "Time a durable accounting operation waits before microbatch collection",
    ["queue"],
    buckets=ACCOUNTING_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)
accounting_database_call_seconds = Histogram(
    "deltallm_accounting_database_call_seconds",
    "Accounting repository call latency including bounded client and pool wait",
    ["operation", "outcome"],
    buckets=ACCOUNTING_LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)
accounting_failures = Counter(
    "deltallm_accounting_failures_total",
    "Accounting protocol failures by bounded phase and reason",
    ["queue", "phase", "reason"],
    registry=get_prometheus_registry(),
)
accounting_reservation_decisions = Counter(
    "deltallm_accounting_reservation_decisions_total",
    "Durable accounting reservation decisions",
    ["decision"],
    registry=get_prometheus_registry(),
)
accounting_permit_actions = Counter(
    "deltallm_accounting_permit_actions_total",
    "Local permit refill, claim, rejection, and retirement results",
    ["action", "outcome"],
    registry=get_prometheus_registry(),
)
accounting_permit_subjects = Gauge(
    "deltallm_accounting_permit_bank_subjects",
    "Subjects in each bounded local permit bank",
    ["lane"],
    registry=get_prometheus_registry(),
)
accounting_permit_available = Gauge(
    "deltallm_accounting_permit_bank_available",
    "Unissued ordinals in each bounded local permit bank",
    ["lane"],
    registry=get_prometheus_registry(),
)
accounting_permit_retained_bytes = Gauge(
    "deltallm_accounting_permit_bank_retained_bytes",
    "Conservative retained-state byte charge in each bounded local permit bank",
    ["lane"],
    registry=get_prometheus_registry(),
)
accounting_projection_actions = Counter(
    "deltallm_accounting_projection_actions_total",
    "Accounting recovery, rollover, and compatibility projection results",
    ["action", "outcome"],
    registry=get_prometheus_registry(),
)
accounting_projection_event_lag = Histogram(
    "deltallm_accounting_projection_event_lag_seconds",
    "Age of an accounting event when compatibility projection completes",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 900, 3600],
    registry=get_prometheus_registry(),
)
accounting_projection_backlog = Gauge(
    "deltallm_accounting_projection_backlog",
    "Durable accounting events beyond projection checkpoints",
    registry=get_prometheus_registry(),
)
accounting_projection_oldest_event_age = Gauge(
    "deltallm_accounting_projection_oldest_event_age_seconds",
    "Age of the oldest durable event beyond a projection checkpoint",
    registry=get_prometheus_registry(),
)


def set_accounting_queue_depth(queue: str, value: int) -> None:
    accounting_queue_depth.labels(queue=queue).set(max(0, int(value)))


def set_accounting_queue_retained_bytes(queue: str, value: int) -> None:
    accounting_queue_retained_bytes.labels(queue=queue).set(max(0, int(value)))


def observe_accounting_batch(queue: str, size: int, seconds: float, outcome: str) -> None:
    accounting_batch_size.labels(queue=queue).observe(max(0, int(size)))
    accounting_batch_seconds.labels(queue=queue, outcome=outcome).observe(max(0.0, float(seconds)))


def observe_accounting_queue_wait(queue: str, seconds: float) -> None:
    accounting_queue_wait_seconds.labels(queue=queue).observe(max(0.0, float(seconds)))


def observe_accounting_database_call(operation: str, seconds: float, outcome: str) -> None:
    accounting_database_call_seconds.labels(operation=operation, outcome=outcome).observe(
        max(0.0, float(seconds))
    )


def increment_accounting_failure(queue: str, phase: str, reason: str) -> None:
    accounting_failures.labels(queue=queue, phase=phase, reason=reason).inc()


def increment_accounting_decision(decision: str) -> None:
    accounting_reservation_decisions.labels(decision=decision).inc()


def increment_accounting_permit_action(action: str, outcome: str, *, count: int = 1) -> None:
    accounting_permit_actions.labels(action=action, outcome=outcome).inc(max(0, int(count)))


def set_accounting_permit_bank(
    lane: int, *, subjects: int, available: int, retained_bytes: int
) -> None:
    accounting_permit_subjects.labels(lane=str(lane)).set(max(0, subjects))
    accounting_permit_available.labels(lane=str(lane)).set(max(0, available))
    accounting_permit_retained_bytes.labels(lane=str(lane)).set(max(0, retained_bytes))


def increment_accounting_projection(action: str, outcome: str, value: int = 1) -> None:
    if value > 0:
        accounting_projection_actions.labels(action=action, outcome=outcome).inc(int(value))


def observe_accounting_projection_lag(seconds: float) -> None:
    accounting_projection_event_lag.observe(max(0.0, float(seconds)))


def set_accounting_projection_backlog(count: int, oldest_age_seconds: float) -> None:
    accounting_projection_backlog.set(max(0, int(count)))
    accounting_projection_oldest_event_age.set(max(0.0, float(oldest_age_seconds)))
