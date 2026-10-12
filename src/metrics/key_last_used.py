"""Fixed-label diagnostics for best-effort API-key last-used tracking."""

from enum import StrEnum

from prometheus_client import Counter

from src.metrics.prometheus import get_prometheus_registry


class KeyLastUsedEvent(StrEnum):
    DROPPED = "dropped"
    FLUSHED = "flushed"
    FLUSH_FAILED = "flush_failed"


_events = Counter(
    "deltallm_key_last_used_events_total",
    "API-key last-used tracking by fixed outcome; values count token hashes",
    ["event"],
    registry=get_prometheus_registry(),
)


def record_key_last_used_event(event: KeyLastUsedEvent, count: int = 1) -> None:
    if count > 0:
        _events.labels(event=event.value).inc(count)
