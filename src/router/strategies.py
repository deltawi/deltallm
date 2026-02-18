from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class DeploymentLike:
    deployment_id: str
    weight: int = 1
    priority: int = 0
    tags: list[str] | None = None
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    rpm_limit: int | None = None
    tpm_limit: int | None = None


class StateBackendLike(Protocol):
    async def get_active_requests_batch(self, deployment_ids: list[str]) -> dict[str, int]: ...

    async def get_latency_windows_batch(
        self,
        deployment_ids: list[str],
        window_ms: int,
    ) -> dict[str, list[tuple[int, float]]]: ...

    async def get_usage_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, int]]: ...


class RoutingStrategyImpl(Protocol):
    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None: ...


def weighted_random_choice(deployments: list[DeploymentLike]) -> DeploymentLike | None:
    if not deployments:
        return None

    weights = [max(0, int(d.weight)) for d in deployments]
    total = sum(weights)
    if total <= 0:
        return random.choice(deployments)

    pick = random.uniform(0, total)
    cumulative = 0.0
    for deployment, weight in zip(deployments, weights, strict=False):
        cumulative += weight
        if pick <= cumulative:
            return deployment

    return deployments[-1]


class SimpleShuffleStrategy:
    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        return weighted_random_choice(deployments)


class LeastBusyStrategy:
    def __init__(self, state_backend: StateBackendLike):
        self.state = state_backend

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        if not deployments:
            return None

        counts = await self.state.get_active_requests_batch([d.deployment_id for d in deployments])
        min_count = min(counts.get(d.deployment_id, 0) for d in deployments)
        candidates = [d for d in deployments if counts.get(d.deployment_id, 0) == min_count]
        return weighted_random_choice(candidates)


class LatencyBasedStrategy:
    def __init__(self, state_backend: StateBackendLike, window_size_ms: int = 300_000):
        self.state = state_backend
        self.window_size_ms = window_size_ms

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        if not deployments:
            return None

        windows = await self.state.get_latency_windows_batch(
            [d.deployment_id for d in deployments],
            window_ms=self.window_size_ms,
        )
        best: DeploymentLike | None = None
        best_latency = float("inf")
        for deployment in deployments:
            avg = self._weighted_avg(windows.get(deployment.deployment_id, []))
            if avg < best_latency:
                best_latency = avg
                best = deployment

        return best or weighted_random_choice(deployments)

    def _weighted_avg(self, window: list[tuple[int, float]]) -> float:
        if not window:
            return float("inf")

        now_ms = time.time() * 1000
        total_weight = 0.0
        weighted_sum = 0.0
        for ts, latency in window:
            age = max(0.0, now_ms - ts)
            weight = math.exp(-age / 60_000)
            weighted_sum += float(latency) * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else float("inf")


class CostBasedStrategy:
    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        if not deployments:
            return None

        return min(
            deployments,
            key=lambda d: float(d.input_cost_per_token) + float(d.output_cost_per_token),
        )


class UsageBasedStrategy:
    def __init__(self, state_backend: StateBackendLike):
        self.state = state_backend

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        if not deployments:
            return None

        usage = await self.state.get_usage_batch([d.deployment_id for d in deployments])
        best: DeploymentLike | None = None
        best_utilization = float("inf")

        for deployment in deployments:
            dep_usage = usage.get(deployment.deployment_id, {})
            rpm_util = self._calc_utilization(dep_usage.get("rpm", 0), deployment.rpm_limit)
            tpm_util = self._calc_utilization(dep_usage.get("tpm", 0), deployment.tpm_limit)
            utilization = max(rpm_util, tpm_util)
            if utilization < best_utilization:
                best_utilization = utilization
                best = deployment

        return best or weighted_random_choice(deployments)

    @staticmethod
    def _calc_utilization(current: int, limit: int | None) -> float:
        if not limit or limit <= 0:
            return 0.0
        return current / limit


class TagBasedStrategy:
    def __init__(self, fallback_strategy: RoutingStrategyImpl | None = None):
        self.fallback = fallback_strategy or SimpleShuffleStrategy()

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        request_tags = context.get("metadata", {}).get("tags") or []
        if not request_tags:
            return await self.fallback.select(deployments, context)

        filtered = [
            d
            for d in deployments
            if d.tags and all(tag in d.tags for tag in request_tags)
        ]
        if not filtered:
            return None

        return await self.fallback.select(filtered, context)


class PriorityBasedStrategy:
    def __init__(self, fallback_strategy: RoutingStrategyImpl | None = None):
        self.fallback = fallback_strategy or SimpleShuffleStrategy()

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        if not deployments:
            return None

        by_priority: dict[int, list[DeploymentLike]] = {}
        for deployment in deployments:
            by_priority.setdefault(int(deployment.priority), []).append(deployment)

        for priority in sorted(by_priority):
            selected = await self.fallback.select(by_priority[priority], context)
            if selected:
                return selected

        return None


class WeightedStrategy:
    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        return weighted_random_choice(deployments)


class RateLimitAwareStrategy:
    def __init__(self, state_backend: StateBackendLike, utilization_threshold: float = 0.9):
        self.state = state_backend
        self.utilization_threshold = utilization_threshold

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        if not deployments:
            return None

        usage = await self.state.get_usage_batch([d.deployment_id for d in deployments])
        available: list[DeploymentLike] = []
        for deployment in deployments:
            dep_usage = usage.get(deployment.deployment_id, {})
            rpm_util = dep_usage.get("rpm", 0) / (deployment.rpm_limit or float("inf"))
            tpm_util = dep_usage.get("tpm", 0) / (deployment.tpm_limit or float("inf"))
            if rpm_util < self.utilization_threshold and tpm_util < self.utilization_threshold:
                available.append(deployment)

        return weighted_random_choice(available)
