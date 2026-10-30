"""Bounded labels for inference deadlines and optional/CPU work allocations."""

from prometheus_client import Counter, Gauge, Histogram

from src.metrics.prometheus import get_prometheus_registry

# These were registered on the client default registry, which /metrics does not
# serve. Use the gateway registry so the documented series are scraped.
_REGISTRY = get_prometheus_registry()

request_deadline_expirations = Counter(
    "deltallm_request_deadline_expirations_total",
    "Total inference deadline expirations",
    ("response",),
    registry=_REGISTRY,
)
work_rejections = Counter(
    "deltallm_bounded_work_rejections_total",
    "Work rejected before execution",
    ("allocation", "reason"),
    registry=_REGISTRY,
)
work_in_flight = Gauge(
    "deltallm_bounded_work_in_flight",
    "Accepted unfinished work",
    ("allocation",),
    registry=_REGISTRY,
)
work_bytes = Gauge(
    "deltallm_bounded_work_bytes",
    "Conservative retained payload allocation",
    ("allocation",),
    registry=_REGISTRY,
)
work_duration = Histogram(
    "deltallm_bounded_work_seconds",
    "Accepted work duration including queue wait",
    ("allocation", "outcome"),
    registry=_REGISTRY,
)
callback_outcomes = Counter(
    "deltallm_callback_outcomes_total",
    "Optional callback delivery outcomes",
    ("integration", "outcome"),
    registry=_REGISTRY,
)
otel_span_outcomes = Counter(
    "deltallm_otel_spans_total",
    "OpenTelemetry spans by export outcome",
    ("outcome",),
    registry=_REGISTRY,
)
otel_span_queue_depth = Gauge(
    "deltallm_otel_span_queue_depth",
    "OpenTelemetry spans waiting for export in this process",
    registry=_REGISTRY,
)
