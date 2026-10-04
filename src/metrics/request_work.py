"""Bounded labels for inference deadlines and optional/CPU work allocations."""

from prometheus_client import Counter, Gauge, Histogram

request_deadline_expirations = Counter(
    "deltallm_request_deadline_expirations_total",
    "Total inference deadline expirations",
    ("response",),
)
work_rejections = Counter(
    "deltallm_bounded_work_rejections_total",
    "Work rejected before execution",
    ("allocation", "reason"),
)
work_in_flight = Gauge(
    "deltallm_bounded_work_in_flight",
    "Accepted unfinished work",
    ("allocation",),
)
work_bytes = Gauge(
    "deltallm_bounded_work_bytes",
    "Conservative retained payload allocation",
    ("allocation",),
)
work_duration = Histogram(
    "deltallm_bounded_work_seconds",
    "Accepted work duration including queue wait",
    ("allocation", "outcome"),
)
callback_outcomes = Counter(
    "deltallm_callback_outcomes_total",
    "Optional callback delivery outcomes",
    ("integration", "outcome"),
)
