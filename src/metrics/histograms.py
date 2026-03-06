from __future__ import annotations

from prometheus_client import Histogram

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

LATENCY_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]

deltallm_request_total_latency_metric = Histogram(
    "deltallm_request_total_latency_seconds",
    "End-to-end request latency",
    ["model", "api_provider", "status_code"],
    buckets=LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_llm_api_latency_metric = Histogram(
    "deltallm_llm_api_latency_seconds",
    "Provider API latency only",
    ["model", "api_provider"],
    buckets=LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)

deltallm_prompt_resolution_latency_metric = Histogram(
    "deltallm_prompt_resolution_latency_seconds",
    "Prompt registry resolution and render latency",
    ["source", "status"],
    buckets=LATENCY_BUCKETS,
    registry=get_prometheus_registry(),
)


def observe_request_latency(*, model: str, api_provider: str, status_code: int, latency_seconds: float) -> None:
    deltallm_request_total_latency_metric.labels(
        model=sanitize_label(model),
        api_provider=sanitize_label(api_provider),
        status_code=str(status_code),
    ).observe(max(0.0, float(latency_seconds)))


def observe_api_latency(*, model: str, api_provider: str, latency_seconds: float) -> None:
    deltallm_llm_api_latency_metric.labels(
        model=sanitize_label(model),
        api_provider=sanitize_label(api_provider),
    ).observe(max(0.0, float(latency_seconds)))


def observe_prompt_resolution_latency(*, source: str, status: str, latency_seconds: float) -> None:
    deltallm_prompt_resolution_latency_metric.labels(
        source=sanitize_label(source),
        status=sanitize_label(status),
    ).observe(max(0.0, float(latency_seconds)))
