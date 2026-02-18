from __future__ import annotations

from prometheus_client import Counter

from src.metrics.prometheus import (
    ANONYMOUS_LABEL,
    DEFAULT_TEAM_LABEL,
    get_prometheus_registry,
    hash_api_key,
    sanitize_label,
)

litellm_requests_metric = Counter(
    "litellm_requests_total",
    "Total LLM API requests",
    ["model", "api_provider", "api_key", "user", "team", "status_code"],
    registry=get_prometheus_registry(),
)

litellm_request_failures_metric = Counter(
    "litellm_request_failures_total",
    "Total failed requests",
    ["model", "api_provider", "error_type"],
    registry=get_prometheus_registry(),
)

litellm_input_tokens_metric = Counter(
    "litellm_input_tokens_total",
    "Total input tokens",
    ["model", "api_provider", "api_key", "user", "team"],
    registry=get_prometheus_registry(),
)

litellm_output_tokens_metric = Counter(
    "litellm_output_tokens_total",
    "Total output tokens",
    ["model", "api_provider", "api_key", "user", "team"],
    registry=get_prometheus_registry(),
)

litellm_spend_metric = Counter(
    "litellm_spend_total",
    "Total spend in USD",
    ["model", "api_provider", "api_key", "user", "team"],
    registry=get_prometheus_registry(),
)

litellm_cache_hit_metric = Counter(
    "litellm_cache_hit_total",
    "Total cache hits",
    ["model", "cache_type"],
    registry=get_prometheus_registry(),
)

litellm_cache_miss_metric = Counter(
    "litellm_cache_miss_total",
    "Total cache misses",
    ["model", "cache_type"],
    registry=get_prometheus_registry(),
)


def increment_request(
    *,
    model: str,
    api_provider: str,
    api_key: str | None,
    user: str | None,
    team: str | None,
    status_code: int,
) -> None:
    litellm_requests_metric.labels(
        model=sanitize_label(model),
        api_provider=sanitize_label(api_provider),
        api_key=hash_api_key(api_key),
        user=sanitize_label(user, ANONYMOUS_LABEL),
        team=sanitize_label(team, DEFAULT_TEAM_LABEL),
        status_code=str(status_code),
    ).inc()


def increment_request_failure(*, model: str, api_provider: str, error_type: str) -> None:
    litellm_request_failures_metric.labels(
        model=sanitize_label(model),
        api_provider=sanitize_label(api_provider),
        error_type=sanitize_label(error_type),
    ).inc()


def increment_usage(
    *,
    model: str,
    api_provider: str,
    api_key: str | None,
    user: str | None,
    team: str | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    labels = {
        "model": sanitize_label(model),
        "api_provider": sanitize_label(api_provider),
        "api_key": hash_api_key(api_key),
        "user": sanitize_label(user, ANONYMOUS_LABEL),
        "team": sanitize_label(team, DEFAULT_TEAM_LABEL),
    }
    litellm_input_tokens_metric.labels(**labels).inc(max(0, int(prompt_tokens)))
    litellm_output_tokens_metric.labels(**labels).inc(max(0, int(completion_tokens)))


def increment_spend(
    *,
    model: str,
    api_provider: str,
    api_key: str | None,
    user: str | None,
    team: str | None,
    spend: float,
) -> None:
    litellm_spend_metric.labels(
        model=sanitize_label(model),
        api_provider=sanitize_label(api_provider),
        api_key=hash_api_key(api_key),
        user=sanitize_label(user, ANONYMOUS_LABEL),
        team=sanitize_label(team, DEFAULT_TEAM_LABEL),
    ).inc(max(0.0, float(spend)))


def increment_cache_hit(*, model: str, cache_type: str) -> None:
    litellm_cache_hit_metric.labels(
        model=sanitize_label(model),
        cache_type=sanitize_label(cache_type),
    ).inc()


def increment_cache_miss(*, model: str, cache_type: str) -> None:
    litellm_cache_miss_metric.labels(
        model=sanitize_label(model),
        cache_type=sanitize_label(cache_type),
    ).inc()
