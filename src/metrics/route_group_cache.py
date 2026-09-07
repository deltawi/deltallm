"""Bounded diagnostics for disposable route-group runtime snapshots."""

from enum import StrEnum

from prometheus_client import Counter

from src.metrics.prometheus import get_prometheus_registry


class RouteGroupCacheFailureReason(StrEnum):
    REDIS_UNAVAILABLE = "redis_unavailable"
    INVALID_PAYLOAD = "invalid_payload"
    OVERSIZED = "oversized"
    REVISION_MISMATCH = "revision_mismatch"
    WRITE_UNAVAILABLE = "write_unavailable"


_failures = Counter(
    "deltallm_route_group_cache_failures_total",
    "Route-group runtime cache degradation by fixed failure reason",
    ["reason"],
    registry=get_prometheus_registry(),
)


def record_route_group_cache_failure(reason: RouteGroupCacheFailureReason) -> None:
    _failures.labels(reason=reason.value).inc()
