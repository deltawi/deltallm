from __future__ import annotations

from prometheus_client import Counter, Gauge

from src.metrics.prometheus import get_prometheus_registry, sanitize_label

prompt_singleflight_inflight_metric = Gauge(
    "deltallm_prompt_singleflight_inflight",
    "Current process-owned prompt singleflight tasks",
    registry=get_prometheus_registry(),
)

prompt_singleflight_outcome_metric = Counter(
    "deltallm_prompt_singleflight_outcomes_total",
    "Prompt singleflight outcomes by bounded result",
    ["outcome"],
    registry=get_prometheus_registry(),
)


def set_prompt_singleflight_inflight(value: int) -> None:
    prompt_singleflight_inflight_metric.set(max(0, int(value)))


def increment_prompt_singleflight_outcome(*, outcome: str) -> None:
    prompt_singleflight_outcome_metric.labels(outcome=sanitize_label(outcome)).inc()
