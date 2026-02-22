from __future__ import annotations

from prometheus_client import Gauge

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

litellm_deployment_state_metric = Gauge(
    "litellm_deployment_state",
    "Deployment health state (0=healthy, 1=partial, 2=degraded)",
    ["deployment_id", "model"],
    registry=get_prometheus_registry(),
)

litellm_deployment_latency_per_output_token_metric = Gauge(
    "litellm_deployment_latency_per_output_token_ms",
    "Average latency per output token",
    ["deployment_id", "model"],
    registry=get_prometheus_registry(),
)

litellm_deployment_active_requests_metric = Gauge(
    "litellm_deployment_active_requests",
    "Current in-flight requests per deployment",
    ["deployment_id", "model"],
    registry=get_prometheus_registry(),
)

litellm_deployment_cooldown_metric = Gauge(
    "litellm_deployment_cooldown",
    "Whether deployment is in cooldown (0/1)",
    ["deployment_id", "model"],
    registry=get_prometheus_registry(),
)


def set_deployment_state(*, deployment_id: str, model: str, state: int) -> None:
    litellm_deployment_state_metric.labels(
        deployment_id=sanitize_label(deployment_id),
        model=sanitize_label(model),
    ).set(float(state))


def set_deployment_latency_per_output_token(*, deployment_id: str, model: str, latency_ms: float) -> None:
    litellm_deployment_latency_per_output_token_metric.labels(
        deployment_id=sanitize_label(deployment_id),
        model=sanitize_label(model),
    ).set(max(0.0, float(latency_ms)))


def set_deployment_active_requests(*, deployment_id: str, model: str, count: int) -> None:
    litellm_deployment_active_requests_metric.labels(
        deployment_id=sanitize_label(deployment_id),
        model=sanitize_label(model),
    ).set(max(0, int(count)))


def set_deployment_cooldown(*, deployment_id: str, model: str, cooldown: bool) -> None:
    litellm_deployment_cooldown_metric.labels(
        deployment_id=sanitize_label(deployment_id),
        model=sanitize_label(model),
    ).set(1.0 if cooldown else 0.0)
