from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol

from src.billing.cost import compute_billing_result


@dataclass
class DeploymentLike:
    deployment_id: str
    weight: int = 1
    priority: int = 0
    tags: list[str] | None = None
    model_info: dict[str, Any] | None = None
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    image_pm_limit: int | None = None
    audio_seconds_pm_limit: int | None = None
    char_pm_limit: int | None = None
    rerank_units_pm_limit: int | None = None


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


def random_choice(deployments: list[DeploymentLike]) -> DeploymentLike | None:
    if not deployments:
        return None
    return random.choice(deployments)


def weighted_random_choice(deployments: list[DeploymentLike]) -> DeploymentLike | None:
    if not deployments:
        return None

    weights = [max(0, int(d.weight)) for d in deployments]
    total = sum(weights)
    if total <= 0:
        return random_choice(deployments)

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
        return random_choice(deployments)


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
        scored: list[tuple[DeploymentLike, float]] = []
        unsampled: list[DeploymentLike] = []
        for deployment in deployments:
            avg = self._weighted_avg(windows.get(deployment.deployment_id, []))
            if math.isfinite(avg):
                scored.append((deployment, avg))
            else:
                unsampled.append(deployment)

        if not scored:
            return weighted_random_choice(deployments)

        best_latency = min(score for _, score in scored)
        candidates = [deployment for deployment, score in scored if score == best_latency]
        if unsampled:
            candidates.extend(unsampled)
        return weighted_random_choice(candidates)

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

        best_deployment: DeploymentLike | None = None
        best_cost = float("inf")
        for deployment in deployments:
            estimated_cost = self._estimated_unit_cost(deployment)
            if estimated_cost < best_cost:
                best_cost = estimated_cost
                best_deployment = deployment
        return best_deployment or weighted_random_choice(deployments)

    @staticmethod
    def _estimated_unit_cost(deployment: DeploymentLike) -> float:
        info = dict(deployment.model_info or {})
        info.setdefault("input_cost_per_token", float(deployment.input_cost_per_token))
        info.setdefault("output_cost_per_token", float(deployment.output_cost_per_token))
        mode = str(info.get("mode") or "chat").strip() or "chat"
        result = compute_billing_result(
            mode=mode,
            usage=CostBasedStrategy._synthetic_usage_for_mode(mode),
            model_info=info,
        )
        if not result.pricing_fields_used:
            return float("inf")
        return float(result.cost)

    @staticmethod
    def _synthetic_usage_for_mode(mode: str) -> dict[str, int | float]:
        if mode == "embedding":
            return {"prompt_tokens": 1, "completion_tokens": 0}
        if mode == "image_generation":
            return {"images": 1}
        if mode == "audio_speech":
            return {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "input_audio_tokens": 1,
                "output_audio_tokens": 1,
                "input_characters": 1,
                "output_characters": 1,
                "duration_seconds": 1.0,
            }
        if mode == "audio_transcription":
            return {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "input_audio_tokens": 1,
                "duration_seconds": 1.0,
                "billable_duration_seconds": 1.0,
            }
        return {"prompt_tokens": 1, "completion_tokens": 1}


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
            utilization = usage_utilization_for_deployment(deployment, dep_usage)
            if utilization < best_utilization:
                best_utilization = utilization
                best = deployment

        return best or weighted_random_choice(deployments)

class TagBasedStrategy:
    def __init__(self, fallback_strategy: RoutingStrategyImpl | None = None):
        self.fallback = fallback_strategy or WeightedStrategy()

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
    ) -> DeploymentLike | None:
        # Request-tag eligibility is already enforced by the router before strategy
        # selection, so tag-based routing is intentionally just weighted selection on
        # the remaining tag-matched pool.
        return await self.fallback.select(deployments, context)


class PriorityBasedStrategy:
    def __init__(self, fallback_strategy: RoutingStrategyImpl | None = None):
        self.fallback = fallback_strategy or WeightedStrategy()

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
            utilization = usage_utilization_for_deployment(deployment, dep_usage)
            if utilization < self.utilization_threshold:
                available.append(deployment)

        return weighted_random_choice(available)


def usage_utilization_for_deployment(
    deployment: DeploymentLike,
    usage: dict[str, int] | None,
) -> float:
    dep_usage = usage or {}
    utilizations: list[float] = []

    rpm_util = _calc_utilization(dep_usage.get("rpm", 0), deployment.rpm_limit)
    if deployment.rpm_limit is not None:
        utilizations.append(rpm_util)

    mode = str((deployment.model_info or {}).get("mode") or "chat").strip().lower() or "chat"
    if mode in {"chat", "embedding"}:
        if deployment.tpm_limit is not None:
            utilizations.append(_calc_utilization(dep_usage.get("tpm", 0), deployment.tpm_limit))
    elif mode == "image_generation":
        if deployment.image_pm_limit is not None:
            utilizations.append(_calc_utilization(dep_usage.get("image_pm", 0), deployment.image_pm_limit))
    elif mode in {"audio_speech", "audio_transcription"}:
        if deployment.audio_seconds_pm_limit is not None:
            utilizations.append(
                _calc_utilization(dep_usage.get("audio_seconds_pm", 0), deployment.audio_seconds_pm_limit)
            )
        if deployment.char_pm_limit is not None:
            utilizations.append(_calc_utilization(dep_usage.get("char_pm", 0), deployment.char_pm_limit))
    elif mode == "rerank":
        if deployment.rerank_units_pm_limit is not None:
            utilizations.append(
                _calc_utilization(dep_usage.get("rerank_units_pm", 0), deployment.rerank_units_pm_limit)
            )
    elif deployment.tpm_limit is not None:
        utilizations.append(_calc_utilization(dep_usage.get("tpm", 0), deployment.tpm_limit))

    return max(utilizations, default=0.0)


def usage_within_limits(
    deployment: DeploymentLike,
    usage: dict[str, int] | None,
) -> bool:
    return usage_utilization_for_deployment(deployment, usage) < 1.0


def _calc_utilization(current: int, limit: int | None) -> float:
    if not limit or limit <= 0:
        return 0.0
    return current / limit
