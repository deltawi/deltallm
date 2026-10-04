"""Process allocations; labels are fixed classes and outcomes, never identities."""

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry

_registry = get_prometheus_registry()
ingress_active = Gauge(
    "deltallm_ingress_active", "Admitted application lifetimes", ["allocation"], registry=_registry
)
ingress_waiters = Gauge(
    "deltallm_ingress_waiters",
    "Requests awaiting ingress admission",
    ["allocation"],
    registry=_registry,
)
ingress_bytes = Gauge(
    "deltallm_ingress_buffered_bytes",
    "Charged raw request body bytes",
    ["allocation"],
    registry=_registry,
)
ingress_rejections = Counter(
    "deltallm_ingress_rejections_total",
    "Local ingress rejections",
    ["allocation", "reason"],
    registry=_registry,
)
ingress_queue_seconds = Histogram(
    "deltallm_ingress_queue_seconds",
    "Ingress admission wait",
    ["allocation", "outcome"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5, 1),
    registry=_registry,
)
auth_tasks = Gauge(
    "deltallm_auth_fallback_tasks",
    "Owned authentication lookups including overdue native work",
    registry=_registry,
)
auth_callers = Gauge(
    "deltallm_auth_fallback_callers",
    "Callers awaiting shared authentication lookups",
    registry=_registry,
)
auth_events = Counter(
    "deltallm_auth_fallback_events_total",
    "Authentication lookup and cache outcomes",
    ["phase", "outcome"],
    registry=_registry,
)
auth_seconds = Histogram(
    "deltallm_auth_fallback_seconds",
    "Authentication caller and owned execution duration",
    ["phase"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5, 1, 5, 30),
    registry=_registry,
)
