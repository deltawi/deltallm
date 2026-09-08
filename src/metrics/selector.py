from enum import StrEnum

from prometheus_client import Counter, Histogram

from src.metrics.prometheus import get_prometheus_registry
from src.router.selection.contracts import SelectorCause, SelectorDecision


class SelectorTermination(StrEnum):
    CANCELLED = "cancelled"
    DEADLINE = "deadline"
    ACCOUNTING_UNAVAILABLE = "accounting_unavailable"
    INVARIANT_FAILURE = "invariant_failure"


class SelectorEscalation(StrEnum):
    CAPACITY = "capacity"
    HEALTH = "health"
    CONTEXT = "context"
    PROVIDER_FAILURE = "provider_failure"
    NO_ELIGIBLE_MEMBER = "no_eligible_member"


cleanup_failures = Counter(
    "deltallm_selector_capacity_cleanup_failures_total",
    "Selector capacity releases left to lease expiry",
    registry=get_prometheus_registry(),
)


def observe_selector_cleanup_failure() -> None:
    cleanup_failures.inc()


decisions = Counter(
    "deltallm_selector_decisions_total",
    "One decision per selector operation",
    ["cause", "rank"],
    registry=get_prometheus_registry(),
)
terminations = Counter(
    "deltallm_selector_terminations_total",
    "Selector operations without a decision",
    ["cause"],
    registry=get_prometheus_registry(),
)
latency = Histogram(
    "deltallm_selector_duration_seconds",
    "Selector latency including prerequisite work",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 2, 5),
    registry=get_prometheus_registry(),
)
escalations = Counter(
    "deltallm_selector_escalations_total",
    "Upward answer-lane escalations",
    ["cause", "rank"],
    registry=get_prometheus_registry(),
)
terminal_lanes = Counter(
    "deltallm_selector_terminal_lane_total",
    "Terminal answer lane",
    ["rank"],
    registry=get_prometheus_registry(),
)
ttft_contribution = Histogram(
    "deltallm_selector_ttft_contribution_seconds",
    "Selector contribution to streaming TTFT",
    buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 2, 5),
    registry=get_prometheus_registry(),
)


def observe_selector_decision(decision: SelectorDecision) -> None:
    if not isinstance(decision.cause, SelectorCause):
        raise ValueError("invalid selector cause")
    decisions.labels(cause=decision.cause.value, rank=_rank(decision.minimum_rank)).inc()
    latency.observe(decision.latency_ms / 1000)


def observe_selector_termination(cause: SelectorTermination, *, seconds: float) -> None:
    if not isinstance(cause, SelectorTermination):
        raise ValueError("invalid selector termination")
    terminations.labels(cause=cause.value).inc()
    latency.observe(max(0, seconds))


def observe_selector_escalation(cause: SelectorEscalation, *, rank: int) -> None:
    if not isinstance(cause, SelectorEscalation):
        raise ValueError("invalid selector escalation")
    escalations.labels(cause=cause.value, rank=_rank(rank)).inc()


def observe_selector_answer(*, rank: int, selector_seconds: float, streaming: bool) -> None:
    terminal_lanes.labels(rank=_rank(rank)).inc()
    if streaming:
        ttft_contribution.observe(max(0, selector_seconds))


def _rank(rank: int) -> str:
    if type(rank) is not int or not 0 <= rank < 8:
        raise ValueError("invalid selector rank")
    return str(rank)
