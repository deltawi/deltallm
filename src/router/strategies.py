from __future__ import annotations

import math
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from src.billing.cost import compute_billing_result
from src.router.candidates import UsageCounterName


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


@dataclass(frozen=True, slots=True)
class StrategyStateQuery:
    active_requests: bool = False
    usage: bool = False
    latency_window_ms: int | None = None


@dataclass(frozen=True, slots=True)
class StrategyStateSnapshot:
    active_requests: Mapping[str, int] | None = None
    usage: Mapping[str, dict[str, int]] | None = None
    latency_windows: Mapping[int, Mapping[str, list[tuple[int, float]]]] | None = None


class RoutingStrategyImpl(Protocol):
    def state_query(self) -> StrategyStateQuery: ...

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]: ...

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
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


def random_order(deployments: list[DeploymentLike]) -> list[DeploymentLike]:
    remaining = list(deployments)
    ordered: list[DeploymentLike] = []
    while remaining:
        selected = random_choice(remaining)
        if selected is None:
            break
        ordered.append(selected)
        remaining.remove(selected)
    return ordered


def weighted_random_order(deployments: list[DeploymentLike]) -> list[DeploymentLike]:
    remaining = list(deployments)
    ordered: list[DeploymentLike] = []
    while remaining:
        selected = weighted_random_choice(remaining)
        if selected is None:
            break
        ordered.append(selected)
        remaining.remove(selected)
    return ordered


class SimpleShuffleStrategy:
    def state_query(self) -> StrategyStateQuery:
        return StrategyStateQuery()

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        del context, state_snapshot
        return random_order(deployments)

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None


class LeastBusyStrategy:
    def __init__(self, state_backend: StateBackendLike):
        self.state = state_backend

    def state_query(self) -> StrategyStateQuery:
        return StrategyStateQuery(active_requests=True)

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        del context
        if not deployments:
            return []

        if state_snapshot is not None and state_snapshot.active_requests is not None:
            counts = state_snapshot.active_requests
        else:
            counts = await self.state.get_active_requests_batch(
                [deployment.deployment_id for deployment in deployments]
            )

        by_count: dict[int, list[DeploymentLike]] = {}
        for deployment in deployments:
            by_count.setdefault(int(counts.get(deployment.deployment_id, 0)), []).append(deployment)

        ordered: list[DeploymentLike] = []
        for count in sorted(by_count):
            ordered.extend(weighted_random_order(by_count[count]))
        return ordered

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None


class LatencyBasedStrategy:
    def __init__(self, state_backend: StateBackendLike, window_size_ms: int = 300_000):
        self.state = state_backend
        self.window_size_ms = window_size_ms

    def state_query(self) -> StrategyStateQuery:
        return StrategyStateQuery(latency_window_ms=self.window_size_ms)

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        del context
        if not deployments:
            return []

        cached_windows = None
        if state_snapshot is not None and state_snapshot.latency_windows is not None:
            cached_windows = state_snapshot.latency_windows.get(self.window_size_ms)
        windows = (
            cached_windows
            if cached_windows is not None
            else await self.state.get_latency_windows_batch(
                [deployment.deployment_id for deployment in deployments],
                window_ms=self.window_size_ms,
            )
        )

        by_latency: dict[float, list[DeploymentLike]] = {}
        unsampled: list[DeploymentLike] = []
        for deployment in deployments:
            average = self._weighted_avg(windows.get(deployment.deployment_id, []))
            if math.isfinite(average):
                by_latency.setdefault(average, []).append(deployment)
            else:
                unsampled.append(deployment)

        if not by_latency:
            return weighted_random_order(deployments)

        ordered: list[DeploymentLike] = []
        latencies = sorted(by_latency)
        first_pool = [*by_latency[latencies[0]], *unsampled]
        ordered.extend(weighted_random_order(first_pool))
        for latency in latencies[1:]:
            ordered.extend(weighted_random_order(by_latency[latency]))
        return ordered

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None

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
    def state_query(self) -> StrategyStateQuery:
        return StrategyStateQuery()

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        del context, state_snapshot
        costs = [(deployment, self._estimated_unit_cost(deployment)) for deployment in deployments]
        if not any(math.isfinite(cost) for _, cost in costs):
            return weighted_random_order(deployments)
        return [deployment for deployment, _ in sorted(costs, key=lambda item: item[1])]

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None

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

    def state_query(self) -> StrategyStateQuery:
        return StrategyStateQuery(usage=True)

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        del context
        if not deployments:
            return []

        if state_snapshot is not None and state_snapshot.usage is not None:
            usage = state_snapshot.usage
        else:
            usage = await self.state.get_usage_batch(
                [deployment.deployment_id for deployment in deployments]
            )
        return sorted(
            deployments,
            key=lambda deployment: usage_utilization_for_deployment(
                deployment,
                usage.get(deployment.deployment_id, {}),
            ),
        )

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None


class TagBasedStrategy:
    def __init__(self, fallback_strategy: RoutingStrategyImpl | None = None):
        self.fallback = fallback_strategy or WeightedStrategy()

    def state_query(self) -> StrategyStateQuery:
        return self.fallback.state_query()

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        # Request-tag eligibility is enforced by Router before strategy ordering.
        return await self.fallback.order(deployments, context, state_snapshot)

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None


class PriorityBasedStrategy:
    def __init__(self, fallback_strategy: RoutingStrategyImpl | None = None):
        self.fallback = fallback_strategy or WeightedStrategy()

    def state_query(self) -> StrategyStateQuery:
        return self.fallback.state_query()

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        by_priority: dict[int, list[DeploymentLike]] = {}
        for deployment in deployments:
            by_priority.setdefault(int(deployment.priority), []).append(deployment)

        ordered: list[DeploymentLike] = []
        for priority in sorted(by_priority):
            ordered.extend(
                await self.fallback.order(
                    by_priority[priority],
                    context,
                    state_snapshot,
                )
            )
        return ordered

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None


class WeightedStrategy:
    def state_query(self) -> StrategyStateQuery:
        return StrategyStateQuery()

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        del context, state_snapshot
        return weighted_random_order(deployments)

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None


class RateLimitAwareStrategy:
    def __init__(self, state_backend: StateBackendLike, utilization_threshold: float = 0.9):
        self.state = state_backend
        self.utilization_threshold = utilization_threshold

    def state_query(self) -> StrategyStateQuery:
        return StrategyStateQuery(usage=True)

    async def order(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> list[DeploymentLike]:
        del context
        if not deployments:
            return []

        if state_snapshot is not None and state_snapshot.usage is not None:
            usage = state_snapshot.usage
        else:
            usage = await self.state.get_usage_batch(
                [deployment.deployment_id for deployment in deployments]
            )
        available = [
            deployment
            for deployment in deployments
            if usage_utilization_for_deployment(
                deployment,
                usage.get(deployment.deployment_id, {}),
            )
            < self.utilization_threshold
        ]
        return weighted_random_order(available)

    async def select(
        self,
        deployments: list[DeploymentLike],
        context: dict[str, Any],
        state_snapshot: StrategyStateSnapshot | None = None,
    ) -> DeploymentLike | None:
        ordered = await self.order(deployments, context, state_snapshot)
        return ordered[0] if ordered else None


def usage_utilization_for_deployment(
    deployment: DeploymentLike,
    usage: dict[str, int] | None,
) -> float:
    dep_usage = usage or {}
    return max(
        (
            _calc_utilization(dep_usage.get(counter, 0), limit)
            for counter, limit in usage_limits_for_deployment(deployment)
        ),
        default=0.0,
    )


def usage_limits_for_deployment(
    deployment: DeploymentLike,
) -> tuple[tuple[UsageCounterName, int], ...]:
    limits: list[tuple[UsageCounterName, int]] = []

    def add(counter: UsageCounterName, limit: int | None) -> None:
        if limit is not None and limit > 0:
            limits.append((counter, limit))

    add("rpm", deployment.rpm_limit)
    mode = str((deployment.model_info or {}).get("mode") or "chat").strip().lower() or "chat"
    if mode in {"chat", "embedding"}:
        add("tpm", deployment.tpm_limit)
    elif mode == "image_generation":
        add("image_pm", deployment.image_pm_limit)
    elif mode in {"audio_speech", "audio_transcription"}:
        add("audio_seconds_pm", deployment.audio_seconds_pm_limit)
        add("char_pm", deployment.char_pm_limit)
    elif mode == "rerank":
        add("rerank_units_pm", deployment.rerank_units_pm_limit)
    else:
        add("tpm", deployment.tpm_limit)
    return tuple(limits)


def usage_within_limits(
    deployment: DeploymentLike,
    usage: dict[str, int] | None,
) -> bool:
    return usage_utilization_for_deployment(deployment, usage) < 1.0


def _calc_utilization(current: int, limit: int | None) -> float:
    if not limit or limit <= 0:
        return 0.0
    return current / limit
