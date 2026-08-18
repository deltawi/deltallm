from __future__ import annotations

from prometheus_client import Histogram

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

REQUEST_PHASE_BUCKETS = [
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    15.0,
    30.0,
    60.0,
]

deltallm_request_phase_latency_metric = Histogram(
    "deltallm_request_phase_latency_seconds",
    "Gateway request latency by bounded request phase",
    ["route", "phase", "outcome", "response_kind"],
    buckets=REQUEST_PHASE_BUCKETS,
    registry=get_prometheus_registry(),
)


def observe_request_phase(
    *,
    route: str,
    phase: str,
    outcome: str,
    response_kind: str,
    latency_seconds: float,
) -> None:
    deltallm_request_phase_latency_metric.labels(
        route=sanitize_label(route),
        phase=sanitize_label(phase),
        outcome=sanitize_label(outcome),
        response_kind=sanitize_label(response_kind),
    ).observe(max(0.0, float(latency_seconds)))
