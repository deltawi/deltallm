"""Bounded ordinary operation transition counters, independent of tenant identity."""

from typing import Literal

from prometheus_client import Counter, Gauge

from src.metrics.prometheus import get_prometheus_registry

_transitions = Counter(
    "deltallm_spend_operation_transitions_total",
    "Durable ordinary operation transitions and receipt acceptances",
    ["state"],
    registry=get_prometheus_registry(),
)


def record_operation_transition(
    state: Literal["dispatched", "accepted", "unknown"], count: int = 1
) -> None:
    if count > 0:
        _transitions.labels(state).inc(count)


_unknown = Gauge(
    "deltallm_spend_operation_unknown",
    "Shared unresolved ordinary operations; aggregate replicas with max",
    registry=get_prometheus_registry(),
)
_observed = Gauge(
    "deltallm_spend_operation_observed_timestamp_seconds",
    "Last successful shared operation observation",
    registry=get_prometheus_registry(),
)


def observe_unknown_operations(count: int) -> None:
    _unknown.set(count)
    _observed.set_to_current_time()
