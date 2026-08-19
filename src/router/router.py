from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from enum import Enum
import logging
from typing import Any, Sequence, cast

from src.config import ModelMode
from src.models.errors import (
    ModelNotFoundError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    ServiceUnavailableError,
)
from src.providers.resolution import provider_supports_mode, resolve_provider
from src.router.candidates import (
    DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    ROUTING_MODE_CONTEXT_KEY,
    AttemptCapacity,
    AttemptCapacityLimit,
    AttemptPermit,
    AttemptRejectionReason,
    RouteCandidatePlan,
    candidate_plan_cache,
)
from src.router.state import DeploymentStateBackend
from src.router.strategies import (
    CostBasedStrategy,
    LatencyBasedStrategy,
    LeastBusyStrategy,
    PriorityBasedStrategy,
    RateLimitAwareStrategy,
    SimpleShuffleStrategy,
    StrategyStateSnapshot,
    TagBasedStrategy,
    UsageBasedStrategy,
    WeightedStrategy,
    usage_limits_for_deployment,
    usage_within_limits,
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
    image_pm_limit: int | None = None
    audio_seconds_pm_limit: int | None = None
    char_pm_limit: int | None = None
    rerank_units_pm_limit: int | None = None


@dataclass
class RouterConfig:
    num_retries: int = 0
    retry_after: float = 0.0
    timeout: float = 600.0
    cooldown_time: int = 60
    allowed_fails: int = 2
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


@dataclass(frozen=True, slots=True)
class _CandidatePlanInput:
    model_group: str
    strategy: RoutingStrategy
    strategy_impl: Any
    candidates: tuple[Deployment, ...]


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
        strategy, _, policy = self._resolve_strategy_for_group(model_group)
        self._attach_route_policy_context(request_context, policy)
        plan = (await self.plan_deployments([model_group], request_context))[model_group]
        selected = plan.deployments[0] if plan.deployments else None
        if plan.candidate_count == 0:
            reason = "no_candidates"
        elif plan.filtered_count == 0:
            reason = "no_eligible_candidates"
        elif selected is None:
            reason = "strategy_returned_none"
        else:
            reason = "selected"
        self._record_route_decision(
            request_context,
            model_group=model_group,
            strategy=strategy.value,
            policy_version=policy.policy_version if policy is not None else None,
            timeout_seconds=policy.timeout_seconds if policy is not None else None,
            retry_max_attempts=policy.retry_max_attempts if policy is not None else None,
            candidate_count=plan.candidate_count,
            healthy_count=plan.healthy_count,
            filtered_count=plan.filtered_count,
            selected_deployment_id=selected.deployment_id if selected is not None else None,
            reason=reason,
        )
        return selected

    async def plan_deployments(
        self,
        model_groups: Sequence[str],
        request_context: dict[str, Any],
    ) -> dict[str, RouteCandidatePlan]:
        groups = list(
            dict.fromkeys(str(group).strip() for group in model_groups if str(group).strip())
        )
        cached = candidate_plan_cache(request_context)
        missing_groups = [group for group in groups if group not in cached]
        if not missing_groups:
            return {group: cached[group] for group in groups}

        pending: list[_CandidatePlanInput] = []
        for group in missing_groups:
            strategy, strategy_impl, _ = self._resolve_strategy_for_group(group)
            pending.append(
                _CandidatePlanInput(
                    model_group=group,
                    strategy=strategy,
                    strategy_impl=strategy_impl,
                    candidates=tuple(await self._get_candidates(group)),
                )
            )

        deployment_ids = list(
            dict.fromkeys(
                deployment.deployment_id for group in pending for deployment in group.candidates
            )
        )
        health, cooldowns, state_snapshot = await self._load_planning_state(
            deployment_ids,
            pending,
        )

        for group in pending:
            healthy = self._filter_healthy(group.candidates, health, cooldowns)
            filtered = self._apply_filters(healthy, request_context)
            if self.config.enable_pre_call_checks:
                filtered = self._apply_pre_call_checks(filtered, state_snapshot.usage or {})
            ordered = await group.strategy_impl.order(
                filtered,
                request_context,
                state_snapshot,
            )
            cached[group.model_group] = RouteCandidatePlan(
                model_group=group.model_group,
                strategy=group.strategy.value,
                deployments=tuple(ordered),
                candidate_count=len(group.candidates),
                healthy_count=len(healthy),
                filtered_count=len(filtered),
            )

        return {group: cached[group] for group in groups}

    async def acquire_attempt(
        self,
        deployment: Deployment,
        request_context: dict[str, Any],
        *,
        lease_ttl_seconds: int = DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    ) -> AttemptPermit:
        if not self._apply_filters([deployment], request_context):
            return AttemptPermit(
                deployment_id=deployment.deployment_id,
                acquired=False,
                rejection_reason=AttemptRejectionReason.STATIC_POLICY,
            )

        capacity = (
            self._attempt_capacity(deployment)
            if self.config.enable_pre_call_checks
            else AttemptCapacity()
        )
        return await self.state.acquire_attempt(
            deployment.deployment_id,
            capacity,
            lease_ttl_seconds=lease_ttl_seconds,
        )

    async def release_attempt(self, permit: AttemptPermit) -> int | None:
        return await self.state.release_attempt(permit)

    async def _get_candidates(self, model_group: str) -> list[Deployment]:
        return list(self.deployment_registry.get(model_group, []))

    async def _load_planning_state(
        self,
        deployment_ids: list[str],
        groups: list[_CandidatePlanInput],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, bool], StrategyStateSnapshot]:
        if not deployment_ids:
            return {}, {}, StrategyStateSnapshot()

        state_queries = [group.strategy_impl.state_query() for group in groups]
        needs_active = any(query.active_requests for query in state_queries)
        needs_usage = self.config.enable_pre_call_checks or any(
            query.usage for query in state_queries
        )
        latency_windows = sorted(
            {
                query.latency_window_ms
                for query in state_queries
                if query.latency_window_ms is not None
            }
        )

        calls: dict[str, Any] = {
            "health": self.state.get_health_batch(deployment_ids),
            "cooldowns": self.state.get_cooldown_batch(deployment_ids),
        }
        if needs_active:
            calls["active"] = self.state.get_active_requests_batch(deployment_ids)
        if needs_usage:
            calls["usage"] = self.state.get_usage_batch(deployment_ids)
        for window_ms in latency_windows:
            calls[f"latency:{window_ms}"] = self.state.get_latency_windows_batch(
                deployment_ids,
                window_ms,
            )

        keys = list(calls)
        values = await asyncio.gather(*(calls[key] for key in keys))
        results = dict(zip(keys, values, strict=True))
        latency = {window_ms: results[f"latency:{window_ms}"] for window_ms in latency_windows}
        snapshot = StrategyStateSnapshot(
            active_requests=results.get("active"),
            usage=results.get("usage"),
            latency_windows=latency or None,
        )
        return results["health"], results["cooldowns"], snapshot

    @staticmethod
    def _filter_healthy(
        candidates: Sequence[Deployment],
        health: dict[str, dict[str, Any]],
        cooldowns: dict[str, bool],
    ) -> list[Deployment]:
        filtered: list[Deployment] = []
        for deployment in candidates:
            if cooldowns.get(deployment.deployment_id, False):
                continue
            dep_health = health.get(deployment.deployment_id, {})
            if dep_health.get("healthy", "true") == "false":
                continue
            filtered.append(deployment)

        return filtered

    def _apply_filters(
        self, deployments: list[Deployment], request_context: dict[str, Any]
    ) -> list[Deployment]:
        if not deployments:
            return []

        metadata = request_context.get("metadata")
        request_tags = metadata.get("tags") if isinstance(metadata, dict) else None
        normalized_tags = (
            [tag.strip() for tag in request_tags if isinstance(tag, str) and tag.strip()]
            if isinstance(request_tags, list)
            else []
        )
        request_mode = str(request_context.get(ROUTING_MODE_CONTEXT_KEY) or "").strip().lower()

        return [
            deployment
            for deployment in deployments
            if (
                not normalized_tags
                or (deployment.tags and all(tag in deployment.tags for tag in normalized_tags))
            )
            and self._supports_request_mode(deployment, request_mode)
        ]

    @staticmethod
    def _supports_request_mode(deployment: Deployment, request_mode: str) -> bool:
        if not request_mode:
            return True
        deployment_mode = str(deployment.model_info.get("mode") or "chat").strip().lower() or "chat"
        if deployment_mode != request_mode:
            return False
        return provider_supports_mode(
            resolve_provider(deployment.deltallm_params),
            cast(ModelMode, request_mode),
        )

    @staticmethod
    def _apply_pre_call_checks(
        deployments: list[Deployment],
        usage: dict[str, dict[str, int]],
    ) -> list[Deployment]:
        if not deployments:
            return []

        candidates: list[Deployment] = []
        for deployment in deployments:
            dep_usage = usage.get(deployment.deployment_id, {})
            if usage_within_limits(deployment, dep_usage):
                candidates.append(deployment)

        return candidates

    @staticmethod
    def _attempt_capacity(deployment: Deployment) -> AttemptCapacity:
        return AttemptCapacity(
            limits=tuple(
                AttemptCapacityLimit(counter=counter, limit=limit)
                for counter, limit in usage_limits_for_deployment(deployment)
            )
        )

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

    def _resolve_strategy_for_group(
        self, model_group: str
    ) -> tuple[RoutingStrategy, Any, RouteGroupPolicy | None]:
        policy = self.config.route_group_policies.get(model_group)
        strategy = (
            policy.strategy if policy is not None and policy.strategy is not None else self.strategy
        )
        return strategy, self._load_strategy(strategy), policy

    @staticmethod
    def _attach_route_policy_context(
        request_context: dict[str, Any], policy: RouteGroupPolicy | None
    ) -> None:
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
            if self.deployment_registry.get(model_group):
                raise ServiceUnavailableError(
                    message=f"No healthy deployments available for model '{model_group}'",
                    code=NO_HEALTHY_DEPLOYMENTS_CODE,
                )
            raise ModelNotFoundError(
                message=f"Model '{model_group}' is not configured", code="model_not_found"
            )
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
                    priority=int(override_priority)
                    if override_priority is not None
                    else base.priority,
                )
            )

        if grouped_deployments:
            registry[group_key] = grouped_deployments

    return registry


def build_route_group_policies(
    route_groups: list[dict[str, Any]] | None,
) -> dict[str, RouteGroupPolicy]:
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
        rpm_limit=(
            int(model_info["rpm_limit"])
            if model_info.get("rpm_limit") is not None
            else params.get("rpm")
        ),
        tpm_limit=(
            int(model_info["tpm_limit"])
            if model_info.get("tpm_limit") is not None
            else params.get("tpm")
        ),
        image_pm_limit=(
            int(model_info["image_pm_limit"])
            if model_info.get("image_pm_limit") is not None
            else None
        ),
        audio_seconds_pm_limit=(
            int(model_info["audio_seconds_pm_limit"])
            if model_info.get("audio_seconds_pm_limit") is not None
            else None
        ),
        char_pm_limit=(
            int(model_info["char_pm_limit"])
            if model_info.get("char_pm_limit") is not None
            else None
        ),
        rerank_units_pm_limit=(
            int(model_info["rerank_units_pm_limit"])
            if model_info.get("rerank_units_pm_limit") is not None
            else None
        ),
    )
