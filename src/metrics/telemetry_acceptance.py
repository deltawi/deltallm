"""Bounded request-side outbox metrics, independent of consumer sink timings."""

from enum import StrEnum

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry
from src.metrics.request_phases import REQUEST_PHASE_BUCKETS


class TelemetryQueue(StrEnum):
    AUDIT = "audit"
    SPEND = "spend"


class AcceptancePhase(StrEnum):
    PREPARE = "prepare"
    ACQUIRE = "acquire"
    LOCK = "lock"
    SQL = "sql"
    COMMIT = "commit"
    ROLLBACK = "rollback"
    TOTAL = "total"


phase_seconds = Histogram(
    "deltallm_telemetry_acceptance_phase_seconds",
    "Outbox acceptance phase duration; acquire includes transaction start, lock includes SQL transport",
    ["queue", "phase", "outcome"],
    buckets=REQUEST_PHASE_BUCKETS,
    registry=get_prometheus_registry(),
)
phase_in_flight = Gauge(
    "deltallm_telemetry_acceptance_in_flight",
    "Local enqueue operations currently in a phase; not physical DB connections",
    ["queue", "phase"],
    registry=get_prometheus_registry(),
)
operations = Counter(
    "deltallm_telemetry_acceptance_operations_total",
    "Repository enqueue outcomes; external transaction scope is not a durable acknowledgment",
    ["queue", "outcome", "transaction_scope"],
    registry=get_prometheus_registry(),
)
failures = Counter(
    "deltallm_telemetry_acceptance_failures_total",
    "Outbox acceptance failures by fixed phase and classified reason",
    ["queue", "phase", "reason"],
    registry=get_prometheus_registry(),
)
events_per_commit = Histogram(
    "deltallm_telemetry_acceptance_events_per_commit",
    "New events accepted per successfully committed owned transaction",
    ["queue"],
    buckets=[0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512],
    registry=get_prometheus_registry(),
)
serialized_bytes = Gauge(
    "deltallm_telemetry_acceptance_serialized_bytes",
    "UTF-8 payload bytes retained by local enqueue calls; excludes caller objects and DB buffers",
    ["queue"],
    registry=get_prometheus_registry(),
)
