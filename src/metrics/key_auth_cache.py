"""Fixed-label diagnostics for authoritative API-key fallback."""

from enum import StrEnum

from prometheus_client import Counter

from src.metrics.prometheus import get_prometheus_registry


class KeyAuthCacheFailureReason(StrEnum):
    READ_UNAVAILABLE = "read_unavailable"
    INVALID_PAYLOAD = "invalid_payload"
    WRITE_UNAVAILABLE = "write_unavailable"


_failures = Counter(
    "deltallm_key_auth_cache_failures_total",
    "API-key cache degradation by fixed failure reason",
    ["reason"],
    registry=get_prometheus_registry(),
)


def record_key_auth_cache_failure(reason: KeyAuthCacheFailureReason) -> None:
    _failures.labels(reason=reason.value).inc()
