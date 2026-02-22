from __future__ import annotations

from typing import Protocol

from prometheus_client import Counter

from src.metrics import increment_cache_hit, increment_cache_miss
from src.metrics.prometheus import get_prometheus_registry


class CacheMetricsProtocol(Protocol):
    def hit(self, *, endpoint: str, model: str) -> None: ...

    def miss(self, *, endpoint: str, model: str) -> None: ...

    def write(self, *, endpoint: str, model: str) -> None: ...

    def error(self, *, operation: str) -> None: ...


class NoopCacheMetrics:
    def hit(self, *, endpoint: str, model: str) -> None:
        return None

    def miss(self, *, endpoint: str, model: str) -> None:
        return None

    def write(self, *, endpoint: str, model: str) -> None:
        return None

    def error(self, *, operation: str) -> None:
        return None


class PrometheusCacheMetrics:
    def __init__(self, cache_type: str = "default") -> None:
        self.cache_type = cache_type
        self._writes = Counter(
            "litellm_cache_write_total",
            "Total cache writes",
            ["endpoint", "model"],
            registry=get_prometheus_registry(),
        )
        self._errors = Counter(
            "litellm_cache_error_total",
            "Total cache errors",
            ["operation"],
            registry=get_prometheus_registry(),
        )

    def hit(self, *, endpoint: str, model: str) -> None:
        del endpoint
        increment_cache_hit(model=model, cache_type=self.cache_type)

    def miss(self, *, endpoint: str, model: str) -> None:
        del endpoint
        increment_cache_miss(model=model, cache_type=self.cache_type)

    def write(self, *, endpoint: str, model: str) -> None:
        self._writes.labels(endpoint=endpoint, model=model).inc()

    def error(self, *, operation: str) -> None:
        self._errors.labels(operation=operation).inc()
