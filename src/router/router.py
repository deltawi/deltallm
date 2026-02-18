from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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
    litellm_params: dict[str, Any]
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
        self._strategy_impl = self._load_strategy(strategy)

    def resolve_model_group(self, model_name: str) -> str:
        return self.config.model_group_alias.get(model_name, model_name)

    async def select_deployment(
        self,
        model_group: str,
        request_context: dict[str, Any],
    ) -> Deployment | None:
        candidates = await self._get_candidates(model_group)
        if not candidates:
            return None

        healthy = await self._filter_healthy(candidates)
        filtered = self._apply_filters(healthy, request_context)

        if self.config.enable_pre_call_checks:
            filtered = await self._apply_pre_call_checks(filtered)

        if not filtered:
            return None

        return await self._strategy_impl.select(filtered, request_context)

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

    def _load_strategy(self, strategy: RoutingStrategy):
        strategies = {
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
        return strategies[strategy]

    def require_deployment(self, model_group: str, deployment: Deployment | None) -> Deployment:
        if deployment is None:
            raise ModelNotFoundError(message=f"No healthy deployments available for model '{model_group}'")
        return deployment


def build_deployment_registry(model_registry: dict[str, list[dict[str, Any]]]) -> dict[str, list[Deployment]]:
    registry: dict[str, list[Deployment]] = {}
    for model_name, entries in model_registry.items():
        deployments: list[Deployment] = []
        for index, entry in enumerate(entries):
            params = dict(entry.get("litellm_params", {}))
            model_info = dict(entry.get("model_info", {}))

            deployment_id = entry.get("deployment_id") or f"{model_name}-{index}"
            deployments.append(
                Deployment(
                    deployment_id=str(deployment_id),
                    model_name=model_name,
                    litellm_params=params,
                    model_info=model_info,
                    weight=int(model_info.get("weight", params.get("weight", 1)) or 1),
                    priority=int(model_info.get("priority", 0) or 0),
                    tags=list(model_info.get("tags", []) or []),
                    input_cost_per_token=float(model_info.get("input_cost_per_token", 0.0) or 0.0),
                    output_cost_per_token=float(model_info.get("output_cost_per_token", 0.0) or 0.0),
                    rpm_limit=(int(model_info["rpm_limit"]) if model_info.get("rpm_limit") is not None else params.get("rpm")),
                    tpm_limit=(int(model_info["tpm_limit"]) if model_info.get("tpm_limit") is not None else params.get("tpm")),
                )
            )
        registry[model_name] = deployments

    return registry
