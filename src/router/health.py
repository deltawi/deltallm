from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.metrics import (
    set_deployment_active_requests,
    set_deployment_cooldown,
    set_deployment_latency_per_output_token,
    set_deployment_state,
)
from src.router.router import Deployment
from src.router.state import DeploymentStateBackend


@dataclass
class HealthCheckConfig:
    enabled: bool = False
    interval_seconds: int = 300
    timeout_seconds: int = 30


class BackgroundHealthChecker:
    def __init__(
        self,
        config: HealthCheckConfig,
        deployment_registry: dict[str, list[Deployment]],
        state_backend: DeploymentStateBackend,
        checker: Callable[[Deployment], Awaitable[bool]],
    ):
        self.config = config
        self.registry = deployment_registry
        self.state = state_backend
        self.checker = checker
        self._running = False

    async def start(self) -> None:
        if not self.config.enabled:
            return

        self._running = True
        while self._running:
            await self._run_health_checks()
            await asyncio.sleep(self.config.interval_seconds)

    def stop(self) -> None:
        self._running = False

    async def _run_health_checks(self) -> None:
        deployments = [d for deps in self.registry.values() for d in deps]
        if not deployments:
            return

        tasks = [self._check_deployment(deployment) for deployment in deployments]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for deployment, result in zip(deployments, results, strict=False):
            if result is True:
                await self.state.set_health(deployment.deployment_id, True)
                if await self.state.is_cooled_down(deployment.deployment_id):
                    await self.state.clear_cooldown(deployment.deployment_id)
            else:
                await self.state.set_health(deployment.deployment_id, False)
                await self.state.record_failure(deployment.deployment_id, str(result))

    async def _check_deployment(self, deployment: Deployment) -> bool:
        return await asyncio.wait_for(
            self.checker(deployment),
            timeout=self.config.timeout_seconds,
        )


class PassiveHealthTracker:
    def __init__(self, state_backend: DeploymentStateBackend, failure_threshold: int = 3):
        self.state = state_backend
        self.failure_threshold = failure_threshold

    async def record_request_outcome(self, deployment_id: str, success: bool, error: str | None = None) -> None:
        if success:
            await self.state.record_success(deployment_id)
            await self.state.set_health(deployment_id, True)
            return

        failures = await self.state.record_failure(deployment_id, error or "request_failed")
        if failures >= self.failure_threshold:
            await self.state.set_health(deployment_id, False)


class HealthEndpointHandler:
    def __init__(
        self,
        deployment_registry: dict[str, list[Deployment]],
        state_backend: DeploymentStateBackend,
    ):
        self.registry = deployment_registry
        self.state = state_backend

    async def get_health_status(self, model_filter: str | None = None) -> dict[str, Any]:
        deployments = [d for deps in self.registry.values() for d in deps]
        if model_filter:
            deployments = [d for d in deployments if d.model_name == model_filter]

        deployment_ids = [deployment.deployment_id for deployment in deployments]
        health = await self.state.get_health_batch(deployment_ids)
        active = await self.state.get_active_requests_batch(deployment_ids)
        latencies = await self.state.get_latency_windows_batch(deployment_ids, 300_000)

        items: list[dict[str, Any]] = []
        healthy_count = 0

        for deployment in deployments:
            dep_health = health.get(deployment.deployment_id, {})
            window = latencies.get(deployment.deployment_id, [])
            avg_latency = None
            if window:
                avg_latency = round(sum(lat for _, lat in window) / len(window), 2)

            in_cooldown = await self.state.is_cooled_down(deployment.deployment_id)
            is_healthy = dep_health.get("healthy", "true") != "false" and not in_cooldown
            if is_healthy:
                healthy_count += 1

            state_value = 0 if is_healthy else 2
            set_deployment_state(deployment_id=deployment.deployment_id, model=deployment.model_name, state=state_value)
            set_deployment_active_requests(
                deployment_id=deployment.deployment_id,
                model=deployment.model_name,
                count=active.get(deployment.deployment_id, 0),
            )
            set_deployment_cooldown(
                deployment_id=deployment.deployment_id,
                model=deployment.model_name,
                cooldown=in_cooldown,
            )
            if avg_latency is not None:
                set_deployment_latency_per_output_token(
                    deployment_id=deployment.deployment_id,
                    model=deployment.model_name,
                    latency_ms=avg_latency,
                )

            items.append(
                {
                    "deployment_id": deployment.deployment_id,
                    "model": deployment.model_name,
                    "healthy": is_healthy,
                    "in_cooldown": in_cooldown,
                    "active_requests": active.get(deployment.deployment_id, 0),
                    "consecutive_failures": int(dep_health.get("consecutive_failures", 0) or 0),
                    "last_error": dep_health.get("last_error") or None,
                    "last_error_at": int(dep_health["last_error_at"]) if dep_health.get("last_error_at") else None,
                    "last_success_at": int(dep_health["last_success_at"]) if dep_health.get("last_success_at") else None,
                    "avg_latency_ms": avg_latency,
                }
            )

        total = len(deployments)
        if total == 0 or healthy_count == total:
            status = "healthy"
        elif healthy_count == 0:
            status = "unhealthy"
        else:
            status = "degraded"

        return {
            "status": status,
            "timestamp": int(time.time()),
            "healthy_count": healthy_count,
            "total_count": total,
            "deployments": items,
        }
