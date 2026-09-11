from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from src.route_group_config import ModelMode, validate_context_routing_workload_mode
from src.router.context_policy import ContextRoutingPolicy, parse_context_routing_policy
from src.router.route_group_validation import normalize_route_group_mode
from src.router.selection.lanes import SelectorLaneRouting, parse_selector_lane_routing


class RoutingStrategy(str, Enum):
    SIMPLE_SHUFFLE = "simple-shuffle"
    LEAST_BUSY = "least-busy"
    LATENCY_BASED = "latency-based-routing"
    COST_BASED = "cost-based-routing"
    USAGE_BASED = "usage-based-routing"
    TAG_BASED = "tag-based-routing"
    PRIORITY_BASED = "priority-based-routing"
    WEIGHTED = "weighted"
    RATE_LIMIT_AWARE = "rate-limit-aware"


@dataclass(frozen=True, slots=True)
class RouteGroupPolicy:
    workload_mode: ModelMode | None = None
    strategy: RoutingStrategy | None = None
    policy_version: int | None = None
    timeout_seconds: float | None = None
    retry_max_attempts: int | None = None
    retryable_error_classes: frozenset[str] | None = None
    context: ContextRoutingPolicy | None = None
    selector: SelectorLaneRouting | None = None

    def failover_overrides(self) -> dict[str, object]:
        overrides: dict[str, object] = {}
        if self.timeout_seconds is not None:
            overrides["timeout_seconds"] = float(self.timeout_seconds)
        if self.retry_max_attempts is not None:
            overrides["retry_max_attempts"] = int(self.retry_max_attempts)
        if self.retryable_error_classes:
            overrides["retryable_error_classes"] = sorted(self.retryable_error_classes)
        return overrides


def build_route_group_policies(
    route_groups: Sequence[Mapping[str, object]] | None,
) -> dict[str, RouteGroupPolicy]:
    policies: dict[str, RouteGroupPolicy] = {}
    for group in route_groups or ():
        key = str(group.get("key") or "").strip()
        if not key or not bool(group.get("enabled", True)):
            continue

        strategy_name = group.get("strategy")
        strategy: RoutingStrategy | None = None
        if isinstance(strategy_name, str) and strategy_name in RoutingStrategy._value2member_map_:
            strategy = RoutingStrategy(strategy_name)

        policy_version = group.get("policy_version")
        context = parse_context_routing_policy(group.get("context"))
        if context is not None:
            validate_context_routing_workload_mode(group.get("mode"))
        policies[key] = RouteGroupPolicy(
            workload_mode=normalize_route_group_mode(group.get("mode")),
            strategy=strategy,
            policy_version=int(policy_version) if policy_version is not None else None,
            timeout_seconds=_extract_timeout_seconds(group.get("timeouts")),
            retry_max_attempts=_extract_retry_max_attempts(group.get("retry")),
            retryable_error_classes=_extract_retryable_error_classes(group.get("retry")),
            context=context,
            selector=parse_selector_lane_routing(group),
        )
    return policies


def _extract_timeout_seconds(timeouts: object) -> float | None:
    if not isinstance(timeouts, dict):
        return None
    global_seconds = timeouts.get("global_seconds")
    if global_seconds is not None:
        try:
            parsed = float(global_seconds)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    global_ms = timeouts.get("global_ms")
    if global_ms is None:
        return None
    try:
        parsed_ms = float(global_ms)
    except (TypeError, ValueError):
        return None
    return (parsed_ms / 1000.0) if parsed_ms > 0 else None


def _extract_retry_max_attempts(retry: object) -> int | None:
    if not isinstance(retry, dict):
        return None
    value = retry.get("max_attempts")
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _extract_retryable_error_classes(retry: object) -> frozenset[str] | None:
    if not isinstance(retry, dict):
        return None
    classes = retry.get("retryable_error_classes")
    if not isinstance(classes, list):
        return None
    normalized = {str(item).strip() for item in classes if str(item).strip()}
    return frozenset(normalized) if normalized else None
