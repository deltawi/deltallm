from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import logging
from typing import Any

from src.models.errors import ModelNotFoundError
from src.router.state import DeploymentStateBackend
from src.router.strategies import (
    CostBasedStrategy,
    LatencyBasedStrategy,
    LeastBusyStrategy,
    PriorityBasedStrategy,
    RateLimitAwareStrategy,
    SimpleShuffleStrategy,
    TagBasedStrategy,
    UsageBasedStrategy,
    WeightedStrategy,
)

logger = logging.getLogger(__name__)


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


@dataclass
class Deployment:
    deployment_id: str
    model_name: str
    deltallm_params: dict[str, Any]
    model_info: dict[str, Any] = field(default_factory=dict)
    weight: int = 1
    priority: int = 0
    tags: list[str] = field(default_factory=list)
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    rpm_limit: int | None = None
    tpm_limit: int | None = None


@dataclass
class RouterConfig:
    num_retries: int = 0
    retry_after: float = 0.0
    timeout: float = 600.0
    cooldown_time: int = 60
    allowed_fails: int = 0
    enable_pre_call_checks: bool = False
    model_group_alias: dict[str, str] = field(default_factory=dict)
    route_group_policies: dict[str, "RouteGroupPolicy"] = field(default_factory=dict)


@dataclass
class RouteGroupPolicy:
    strategy: RoutingStrategy | None = None
    policy_version: int | None = None
    timeout_seconds: float | None = None
    retry_max_attempts: int | None = None
    retryable_error_classes: frozenset[str] | None = None

    def failover_overrides(self) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        if self.timeout_seconds is not None:
            overrides["timeout_seconds"] = float(self.timeout_seconds)
        if self.retry_max_attempts is not None:
            overrides["retry_max_attempts"] = int(self.retry_max_attempts)
        if self.retryable_error_classes:
            overrides["retryable_error_classes"] = sorted(self.retryable_error_classes)
        return overrides


class Router:
    def __init__(
        self,
        strategy: RoutingStrategy,
        state_backend: DeploymentStateBackend,
        config: RouterConfig,
        deployment_registry: dict[str, list[Deployment]],
    ):
        self.strategy = strategy
        self.state = state_backend
        self.config = config
        self.deployment_registry = deployment_registry
        self._strategies = self._build_strategy_map()
        self._strategy_impl = self._load_strategy(strategy)

    def resolve_model_group(self, model_name: str) -> str:
        return self.config.model_group_alias.get(model_name, model_name)

    async def select_deployment(
        self,
        model_group: str,
        request_context: dict[str, Any],
    ) -> Deployment | None:
        candidates = await self._get_candidates(model_group)
        strategy, strategy_impl, policy = self._resolve_strategy_for_group(model_group)
        self._attach_route_policy_context(request_context, policy)

        if not candidates:
            self._record_route_decision(
                request_context,
                model_group=model_group,
                strategy=strategy.value,
                policy_version=policy.policy_version if policy is not None else None,
                timeout_seconds=policy.timeout_seconds if policy is not None else None,
                retry_max_attempts=policy.retry_max_attempts if policy is not None else None,
                candidate_count=0,
                healthy_count=0,
                filtered_count=0,
                selected_deployment_id=None,
                reason="no_candidates",
            )
            return None

        healthy = await self._filter_healthy(candidates)
        filtered = self._apply_filters(healthy, request_context)

        if self.config.enable_pre_call_checks:
            filtered = await self._apply_pre_call_checks(filtered)

        if not filtered:
            self._record_route_decision(
                request_context,
                model_group=model_group,
                strategy=strategy.value,
                policy_version=policy.policy_version if policy is not None else None,
                timeout_seconds=policy.timeout_seconds if policy is not None else None,
                retry_max_attempts=policy.retry_max_attempts if policy is not None else None,
                candidate_count=len(candidates),
                healthy_count=len(healthy),
                filtered_count=0,
                selected_deployment_id=None,
                reason="no_eligible_candidates",
            )
            return None

        selected = await strategy_impl.select(filtered, request_context)
        self._record_route_decision(
            request_context,
            model_group=model_group,
            strategy=strategy.value,
            policy_version=policy.policy_version if policy is not None else None,
            timeout_seconds=policy.timeout_seconds if policy is not None else None,
            retry_max_attempts=policy.retry_max_attempts if policy is not None else None,
            candidate_count=len(candidates),
            healthy_count=len(healthy),
            filtered_count=len(filtered),
            selected_deployment_id=selected.deployment_id if selected is not None else None,
            reason="selected" if selected is not None else "strategy_returned_none",
        )
        return selected

    async def _get_candidates(self, model_group: str) -> list[Deployment]:
        return list(self.deployment_registry.get(model_group, []))

    async def _filter_healthy(self, candidates: list[Deployment]) -> list[Deployment]:
        if not candidates:
            return []

        ids = [d.deployment_id for d in candidates]
        health = await self.state.get_health_batch(ids)
        filtered: list[Deployment] = []
        for deployment in candidates:
            if await self.state.is_cooled_down(deployment.deployment_id):
                continue
            dep_health = health.get(deployment.deployment_id, {})
            if dep_health.get("healthy", "true") == "false":
                continue
            filtered.append(deployment)

        return filtered

    def _apply_filters(self, deployments: list[Deployment], request_context: dict[str, Any]) -> list[Deployment]:
        if not deployments:
            return []

        tags = request_context.get("metadata", {}).get("tags") or []
        filtered = deployments
        if tags:
            filtered = [d for d in filtered if d.tags and all(tag in d.tags for tag in tags)]

        if not filtered:
            return []

        top_priority = min(int(d.priority) for d in filtered)
        return [d for d in filtered if int(d.priority) == top_priority]

    async def _apply_pre_call_checks(self, deployments: list[Deployment]) -> list[Deployment]:
        if not deployments:
            return []

        usage = await self.state.get_usage_batch([d.deployment_id for d in deployments])
        candidates: list[Deployment] = []
        for deployment in deployments:
            dep_usage = usage.get(deployment.deployment_id, {})
            rpm = dep_usage.get("rpm", 0)
            tpm = dep_usage.get("tpm", 0)

            rpm_ok = deployment.rpm_limit is None or rpm < deployment.rpm_limit
            tpm_ok = deployment.tpm_limit is None or tpm < deployment.tpm_limit
            if rpm_ok and tpm_ok:
                candidates.append(deployment)

        return candidates

    def _build_strategy_map(self):
        return {
            RoutingStrategy.SIMPLE_SHUFFLE: SimpleShuffleStrategy(),
            RoutingStrategy.LEAST_BUSY: LeastBusyStrategy(self.state),
            RoutingStrategy.LATENCY_BASED: LatencyBasedStrategy(self.state),
            RoutingStrategy.COST_BASED: CostBasedStrategy(),
            RoutingStrategy.USAGE_BASED: UsageBasedStrategy(self.state),
            RoutingStrategy.TAG_BASED: TagBasedStrategy(),
            RoutingStrategy.PRIORITY_BASED: PriorityBasedStrategy(),
            RoutingStrategy.WEIGHTED: WeightedStrategy(),
            RoutingStrategy.RATE_LIMIT_AWARE: RateLimitAwareStrategy(self.state),
        }

    def _load_strategy(self, strategy: RoutingStrategy):
        return self._strategies[strategy]

    def _resolve_strategy_for_group(self, model_group: str) -> tuple[RoutingStrategy, Any, RouteGroupPolicy | None]:
        policy = self.config.route_group_policies.get(model_group)
        strategy = policy.strategy if policy is not None and policy.strategy is not None else self.strategy
        return strategy, self._load_strategy(strategy), policy

    @staticmethod
    def _attach_route_policy_context(request_context: dict[str, Any], policy: RouteGroupPolicy | None) -> None:
        if policy is None:
            request_context.pop("route_policy", None)
            return
        overrides = policy.failover_overrides()
        if overrides:
            request_context["route_policy"] = overrides
            return
        request_context.pop("route_policy", None)

    def _record_route_decision(
        self,
        request_context: dict[str, Any],
        *,
        model_group: str,
        strategy: str,
        policy_version: int | None,
        timeout_seconds: float | None,
        retry_max_attempts: int | None,
        candidate_count: int,
        healthy_count: int,
        filtered_count: int,
        selected_deployment_id: str | None,
        reason: str,
    ) -> None:
        decision = {
            "model_group": model_group,
            "strategy": strategy,
            "policy_version": policy_version,
            "timeout_seconds": timeout_seconds,
            "retry_max_attempts": retry_max_attempts,
            "candidate_count": candidate_count,
            "healthy_count": healthy_count,
            "filtered_count": filtered_count,
            "selected_deployment_id": selected_deployment_id,
            "reason": reason,
        }
        request_context["route_decision"] = decision
        logger.debug(
            "route decision: group=%s strategy=%s selected=%s reason=%s candidates=%s healthy=%s filtered=%s",
            model_group,
            strategy,
            selected_deployment_id,
            reason,
            candidate_count,
            healthy_count,
            filtered_count,
        )

    def require_deployment(self, model_group: str, deployment: Deployment | None) -> Deployment:
        if deployment is None:
            raise ModelNotFoundError(message=f"No healthy deployments available for model '{model_group}'")
        return deployment


def build_deployment_registry(
    model_registry: dict[str, list[dict[str, Any]]],
    route_groups: list[dict[str, Any]] | None = None,
) -> dict[str, list[Deployment]]:
    return build_deployment_registry_with_route_groups(model_registry, route_groups=route_groups)


def build_deployment_registry_with_route_groups(
    model_registry: dict[str, list[dict[str, Any]]],
    route_groups: list[dict[str, Any]] | None,
) -> dict[str, list[Deployment]]:
    registry: dict[str, list[Deployment]] = {}
    deployments_by_id: dict[str, Deployment] = {}

    for model_name, entries in model_registry.items():
        deployments: list[Deployment] = []
        for index, entry in enumerate(entries):
            deployment = _deployment_from_entry(model_name, entry, index)
            deployments.append(deployment)
            deployments_by_id[deployment.deployment_id] = deployment
        registry[model_name] = deployments

    if not route_groups:
        return registry

    for group in route_groups:
        group_key = str(group.get("key") or "").strip()
        if not group_key or not bool(group.get("enabled", True)):
            continue

        members = group.get("members") or []
        grouped_deployments: list[Deployment] = []
        for member in members:
            if not isinstance(member, dict) or not bool(member.get("enabled", True)):
                continue
            deployment_id = str(member.get("deployment_id") or "").strip()
            if not deployment_id:
                continue
            base = deployments_by_id.get(deployment_id)
            if base is None:
                continue

            override_weight = member.get("weight")
            override_priority = member.get("priority")
            grouped_deployments.append(
                replace(
                    base,
                    weight=int(override_weight) if override_weight is not None else base.weight,
                    priority=int(override_priority) if override_priority is not None else base.priority,
                )
            )

        if grouped_deployments:
            registry[group_key] = grouped_deployments

    return registry


def build_route_group_policies(route_groups: list[dict[str, Any]] | None) -> dict[str, RouteGroupPolicy]:
    policies: dict[str, RouteGroupPolicy] = {}
    if not route_groups:
        return policies

    for group in route_groups:
        key = str(group.get("key") or "").strip()
        if not key or not bool(group.get("enabled", True)):
            continue

        strategy_name = group.get("strategy")
        strategy: RoutingStrategy | None = None
        if isinstance(strategy_name, str) and strategy_name in RoutingStrategy._value2member_map_:
            strategy = RoutingStrategy(strategy_name)

        policy_version = group.get("policy_version")
        timeouts = group.get("timeouts")
        retry = group.get("retry")
        policies[key] = RouteGroupPolicy(
            strategy=strategy,
            policy_version=int(policy_version) if policy_version is not None else None,
            timeout_seconds=_extract_timeout_seconds(timeouts),
            retry_max_attempts=_extract_retry_max_attempts(retry),
            retryable_error_classes=_extract_retryable_error_classes(retry),
        )
    return policies


def _extract_timeout_seconds(timeouts: Any) -> float | None:
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


def _extract_retry_max_attempts(retry: Any) -> int | None:
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


def _extract_retryable_error_classes(retry: Any) -> frozenset[str] | None:
    if not isinstance(retry, dict):
        return None
    classes = retry.get("retryable_error_classes")
    if not isinstance(classes, list):
        return None
    normalized = {str(item).strip() for item in classes if str(item).strip()}
    return frozenset(normalized) if normalized else None


def _deployment_from_entry(model_name: str, entry: dict[str, Any], index: int) -> Deployment:
    params = dict(entry.get("deltallm_params", {}))
    model_info = dict(entry.get("model_info", {}))
    deployment_id = entry.get("deployment_id") or f"{model_name}-{index}"
    return Deployment(
        deployment_id=str(deployment_id),
        model_name=model_name,
        deltallm_params=params,
        model_info=model_info,
        weight=int(model_info.get("weight", params.get("weight", 1)) or 1),
        priority=int(model_info.get("priority", 0) or 0),
        tags=list(model_info.get("tags", []) or []),
        input_cost_per_token=float(model_info.get("input_cost_per_token", 0.0) or 0.0),
        output_cost_per_token=float(model_info.get("output_cost_per_token", 0.0) or 0.0),
        rpm_limit=(int(model_info["rpm_limit"]) if model_info.get("rpm_limit") is not None else params.get("rpm")),
        tpm_limit=(int(model_info["tpm_limit"]) if model_info.get("tpm_limit") is not None else params.get("tpm")),
    )
