from __future__ import annotations

import asyncio
import logging
import time
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.metrics import (
    set_deployment_active_requests,
    set_deployment_cooldown,
    set_deployment_latency_per_output_token,
    set_deployment_state,
)
from src.models.errors import ServiceUnavailableError
from src.providers.healthcheck import HealthProbeResult
from src.router.cooldown import CooldownManager
from src.router.health_state import HealthProbeClaim
from src.router.redis_keys import RouterHealthProbeScope
from src.router.router import Deployment
from src.router.registry import DeploymentRegistryStore
from src.router.state import DeploymentStateBackend


logger = logging.getLogger(__name__)


class HealthCheckInProgressError(RuntimeError):
    """Raised when another replica owns the requested coordinated probe."""


@dataclass
class HealthCheckConfig:
    enabled: bool = False
    interval_seconds: int = 300
    timeout_seconds: int = 30
    max_concurrency: int = 10


def _unique_deployments(
    registry: Mapping[str, Sequence[Deployment]],
) -> list[Deployment]:
    unique: dict[str, Deployment] = {}
    for deployments in registry.values():
        for deployment in deployments:
            unique.setdefault(deployment.deployment_id, deployment)
    return list(unique.values())


class BackgroundHealthChecker:
    def __init__(
        self,
        config: HealthCheckConfig,
        deployment_registry: Mapping[str, Sequence[Deployment]] | DeploymentRegistryStore,
        health_manager: CooldownManager | DeploymentStateBackend | None = None,
        checker: Callable[[Deployment], Awaitable[HealthProbeResult]] | None = None,
        *,
        state_backend: DeploymentStateBackend | None = None,
    ):
        if health_manager is not None and state_backend is not None:
            raise TypeError("Pass health_manager or state_backend, not both")
        if checker is None:
            raise TypeError("checker is required")
        resolved_health_manager = health_manager if health_manager is not None else state_backend
        if resolved_health_manager is None:
            raise TypeError("health_manager is required")
        if not isinstance(resolved_health_manager, CooldownManager):
            warnings.warn(
                "Passing state_backend to BackgroundHealthChecker is deprecated; "
                "pass CooldownManager(state_backend) as health_manager instead",
                DeprecationWarning,
                stacklevel=2,
            )
            # The legacy checker marked a deployment unhealthy on its first
            # health-affecting failure, so retain that threshold while adapting
            # the call through the single fenced transition owner.
            resolved_health_manager = CooldownManager(resolved_health_manager, allowed_fails=0)
        self.config = config
        self.registry = (
            deployment_registry
            if isinstance(deployment_registry, DeploymentRegistryStore)
            else DeploymentRegistryStore(deployment_registry)
        )
        self.health = resolved_health_manager
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
        deployments = _unique_deployments(self.registry)
        if not deployments:
            return

        max_concurrency = max(1, int(self.config.max_concurrency))
        for offset in range(0, len(deployments), max_concurrency):
            batch = deployments[offset : offset + max_concurrency]
            results = await asyncio.gather(
                *(self._run_coordinated_check(deployment) for deployment in batch),
                return_exceptions=True,
            )
            for deployment, result in zip(batch, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning(
                        "background deployment health check failed deployment_id=%s error_type=%s",
                        deployment.deployment_id,
                        type(result).__name__,
                    )

    async def _run_coordinated_check(
        self,
        deployment: Deployment,
    ) -> None:
        claim_ttl = max(
            1,
            int(self.config.interval_seconds),
            int(self.config.timeout_seconds) + 1,
        )
        await self._execute_coordinated_check(
            deployment,
            claim_ttl=claim_ttl,
            scope="background",
            release_probe=False,
        )

    async def _execute_coordinated_check(
        self,
        deployment: Deployment,
        *,
        claim_ttl: int,
        scope: RouterHealthProbeScope,
        release_probe: bool,
    ) -> HealthProbeResult | None:
        claim = await self.health.claim_probe(
            deployment.health_ref,
            claim_ttl,
            scope=scope,
        )
        if claim is None:
            return None
        recovery_needs_release = claim.recovery_token is not None
        try:
            try:
                result = await self._check_deployment(deployment)
            except Exception as exc:
                result = HealthProbeResult(
                    healthy=False,
                    error=str(exc) or "Health check failed",
                )
            await self._apply_result(
                deployment,
                result,
                recovery_token=claim.recovery_token,
            )
            recovery_needs_release = result.affects_deployment_health is False
            return result
        finally:
            await self._release_claim(
                deployment,
                claim,
                release_recovery=recovery_needs_release,
                release_probe=release_probe,
            )

    async def check_deployment_once(self, deployment: Deployment) -> HealthProbeResult:
        result = await self._execute_coordinated_check(
            deployment,
            claim_ttl=max(1, int(self.config.timeout_seconds) + 1),
            scope="manual",
            release_probe=True,
        )
        if result is None:
            raise HealthCheckInProgressError("A deployment health check is already in progress")
        return result

    async def _release_claim(
        self,
        deployment: Deployment,
        claim: HealthProbeClaim,
        *,
        release_recovery: bool,
        release_probe: bool,
    ) -> None:
        if release_recovery and claim.recovery_token is not None:
            try:
                await self.health.release_recovery(
                    deployment.health_ref,
                    claim.recovery_token,
                )
            except Exception:
                logger.warning(
                    "health recovery release failed deployment_id=%s",
                    deployment.deployment_id,
                    exc_info=True,
                )
        if release_probe:
            try:
                await self.health.release_probe(deployment.health_ref, claim)
            except Exception:
                logger.warning(
                    "manual health probe release failed deployment_id=%s",
                    deployment.deployment_id,
                    exc_info=True,
                )

    async def _check_deployment(self, deployment: Deployment) -> HealthProbeResult:
        return await asyncio.wait_for(
            self.checker(deployment),
            timeout=self.config.timeout_seconds,
        )

    async def _apply_result(
        self,
        deployment: Deployment,
        result: HealthProbeResult,
        *,
        recovery_token: str | None = None,
    ) -> None:
        if result.healthy:
            await self.health.record_success(
                deployment.health_ref,
                recovery_token=recovery_token,
            )
            return

        if result.affects_deployment_health is False:
            return

        await self.health.record_failure(
            deployment.health_ref,
            result.error or "Health check failed",
            affects_health=True,
            recovery_token=recovery_token,
        )


class HealthEndpointHandler:
    def __init__(
        self,
        deployment_registry: Mapping[str, Sequence[Deployment]] | DeploymentRegistryStore,
        state_backend: DeploymentStateBackend,
    ):
        self.registry = (
            deployment_registry
            if isinstance(deployment_registry, DeploymentRegistryStore)
            else DeploymentRegistryStore(deployment_registry)
        )
        self.state = state_backend

    async def get_health_status(self, model_filter: str | None = None) -> dict[str, Any]:
        deployments = _unique_deployments(self.registry)
        if model_filter:
            deployments = [d for d in deployments if d.model_name == model_filter]

        deployment_ids = [deployment.deployment_id for deployment in deployments]
        health_refs = [deployment.health_ref for deployment in deployments]
        backend_status_fn = getattr(self.state, "get_backend_status", None)
        try:
            health, cooldowns, active, latencies = await asyncio.gather(
                self.state.get_health_batch(health_refs),
                self.state.get_cooldown_batch(health_refs),
                self.state.get_active_requests_batch(deployment_ids),
                self.state.get_latency_windows_batch(deployment_ids, 300_000),
            )
        except ServiceUnavailableError as exc:
            backend_status = backend_status_fn() if callable(backend_status_fn) else None
            return {
                "status": "unhealthy",
                "timestamp": int(time.time()),
                "healthy_count": 0,
                "total_count": len(deployments),
                "deployments": [
                    {
                        "deployment_id": deployment.deployment_id,
                        "model": deployment.model_name,
                        "healthy": False,
                        "in_cooldown": False,
                        "active_requests": 0,
                        "consecutive_failures": 0,
                        "last_error": str(exc),
                        "last_error_at": None,
                        "last_success_at": None,
                        "avg_latency_ms": None,
                    }
                    for deployment in deployments
                ],
                "state_backend": backend_status,
            }
        backend_status = backend_status_fn() if callable(backend_status_fn) else None

        items: list[dict[str, Any]] = []
        healthy_count = 0

        for deployment in deployments:
            dep_health = health.get(deployment.deployment_id, {})
            window = latencies.get(deployment.deployment_id, [])
            avg_latency = None
            if window:
                avg_latency = round(sum(lat for _, lat in window) / len(window), 2)

            in_cooldown = cooldowns.get(deployment.deployment_id, False)
            is_healthy = dep_health.get("healthy", "true") != "false" and not in_cooldown
            if is_healthy:
                healthy_count += 1

            state_value = 0 if is_healthy else 2
            set_deployment_state(
                deployment_id=deployment.deployment_id,
                model=deployment.model_name,
                state=state_value,
            )
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
                    "last_error_at": int(dep_health["last_error_at"])
                    if dep_health.get("last_error_at")
                    else None,
                    "last_success_at": int(dep_health["last_success_at"])
                    if dep_health.get("last_success_at")
                    else None,
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

        if (
            backend_status is not None
            and backend_status.get("mode") != "redis"
            and status == "healthy"
        ):
            status = "degraded"

        payload = {
            "status": status,
            "timestamp": int(time.time()),
            "healthy_count": healthy_count,
            "total_count": total,
            "deployments": items,
        }
        if backend_status is not None:
            payload["state_backend"] = backend_status
        return payload
