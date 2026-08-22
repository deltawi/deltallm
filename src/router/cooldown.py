from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from src.metrics import increment_router_health_transition
from src.router.health_policy import affects_deployment_health
from src.router.health_state import HealthProbeClaim, HealthRefInput, coerce_health_ref
from src.router.redis_keys import RouterHealthProbeScope
from src.router.state import DeploymentStateBackend


class CooldownManager:
    def __init__(
        self,
        state_backend: DeploymentStateBackend,
        cooldown_time: int = 60,
        allowed_fails: int = 2,
        alert_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ):
        self.state = state_backend
        self.cooldown_time = cooldown_time
        self.allowed_fails = allowed_fails
        self.alert_callback = alert_callback

    async def record_failure(
        self,
        health_ref: HealthRefInput,
        error: str,
        *,
        exc: Exception | None = None,
        affects_health: bool | None = None,
        recovery_token: str | None = None,
    ) -> bool:
        resolved_ref = coerce_health_ref(health_ref)
        if affects_health is None and exc is not None:
            affects_health = affects_deployment_health(exc)
        if affects_health is False:
            return False
        transition = await self.state.apply_health_failure(
            resolved_ref,
            error,
            allowed_fails=self.allowed_fails,
            cooldown_seconds=self.cooldown_time,
            recovery_token=recovery_token,
        )
        if transition.entered_cooldown:
            increment_router_health_transition(transition="cooldown")
        if transition.entered_cooldown and self.alert_callback:
            await self.alert_callback(
                {
                    "alert_type": "cooldown_deployment",
                    "deployment_id": resolved_ref.deployment_id,
                    "reason": error,
                    "failure_count": transition.failure_count,
                    "cooldown_until": time.time() + self.cooldown_time,
                }
            )
        return transition.entered_cooldown

    async def record_success(
        self,
        health_ref: HealthRefInput,
        *,
        recovery_token: str | None = None,
    ) -> None:
        transition = await self.state.apply_health_success(
            health_ref,
            recovery_token=recovery_token,
        )
        if transition.recovered:
            increment_router_health_transition(transition="recovered")

    async def claim_probe(
        self,
        health_ref: HealthRefInput,
        ttl_seconds: int,
        *,
        scope: RouterHealthProbeScope = "background",
    ) -> HealthProbeClaim | None:
        return await self.state.claim_health_probe(
            health_ref,
            ttl_seconds,
            scope=scope,
        )

    async def release_probe(self, health_ref: HealthRefInput, claim: HealthProbeClaim) -> None:
        await self.state.release_health_probe(health_ref, claim)

    async def release_recovery(self, health_ref: HealthRefInput, owner_token: str) -> None:
        await self.state.release_health_recovery(health_ref, owner_token)

    async def check_cooldown(self, health_ref: HealthRefInput) -> dict[str, Any] | None:
        if not await self.state.is_cooled_down(health_ref):
            return None

        health = await self.state.get_health(health_ref)
        return {
            "in_cooldown": True,
            "consecutive_failures": int(health.get("consecutive_failures", 0) or 0),
            "last_error": health.get("last_error"),
            "last_error_at": health.get("last_error_at"),
        }

    async def manual_cooldown(
        self, health_ref: HealthRefInput, duration_sec: int, reason: str = "manual"
    ) -> None:
        resolved_ref = coerce_health_ref(health_ref)
        duration = max(1, int(duration_sec))
        await self.state.apply_manual_cooldown(resolved_ref, duration, reason)
        increment_router_health_transition(transition="manual_cooldown")
        if self.alert_callback:
            await self.alert_callback(
                {
                    "alert_type": "cooldown_deployment",
                    "deployment_id": resolved_ref.deployment_id,
                    "reason": reason,
                    "failure_count": 0,
                    "cooldown_until": time.time() + duration,
                }
            )
