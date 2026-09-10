from enum import StrEnum

from prometheus_client import Counter, Histogram


class DiscoveryOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    POLICY = "policy_denied"
    CAPACITY = "capacity"
    DNS = "dns_failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


_operations = Counter(
    "deltallm_provider_discovery_total", "Bounded provider discovery operations.", ("outcome",)
)
_latency = Histogram(
    "deltallm_provider_discovery_seconds",
    "Provider discovery including admission, DNS, HTTP and cleanup.",
    buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10),
)


def record_discovery(outcome: DiscoveryOutcome, seconds: float) -> None:
    _operations.labels(outcome=outcome.value).inc()
    _latency.observe(seconds)
