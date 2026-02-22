from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from src.router.state import DeploymentStateBackend


class CooldownManager:
    def __init__(
        self,
        state_backend: DeploymentStateBackend,
        cooldown_time: int = 60,
        allowed_fails: int = 0,
        alert_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ):
        self.state = state_backend
        self.cooldown_time = cooldown_time
        self.allowed_fails = allowed_fails
        self.alert_callback = alert_callback

    async def record_failure(self, deployment_id: str, error: str) -> bool:
        failure_count = await self.state.record_failure(deployment_id, error)
        if failure_count > self.allowed_fails:
            await self._enter_cooldown(deployment_id, error, failure_count)
            return True
        return False

    async def record_success(self, deployment_id: str) -> None:
        await self.state.record_success(deployment_id)
        await self.state.set_health(deployment_id, True)

    async def _enter_cooldown(self, deployment_id: str, reason: str, failure_count: int) -> None:
        await self.state.set_cooldown(deployment_id, self.cooldown_time, reason)
        await self.state.set_health(deployment_id, False)
        if self.alert_callback:
            await self.alert_callback(
                {
                    "alert_type": "cooldown_deployment",
                    "deployment_id": deployment_id,
                    "reason": reason,
                    "failure_count": failure_count,
                    "cooldown_until": time.time() + self.cooldown_time,
                }
            )

    async def check_cooldown(self, deployment_id: str) -> dict[str, Any] | None:
        if not await self.state.is_cooled_down(deployment_id):
            return None

        health = await self.state.get_health(deployment_id)
        return {
            "in_cooldown": True,
            "consecutive_failures": int(health.get("consecutive_failures", 0) or 0),
            "last_error": health.get("last_error"),
            "last_error_at": health.get("last_error_at"),
        }

    async def manual_cooldown(self, deployment_id: str, duration_sec: int, reason: str = "manual") -> None:
        original = self.cooldown_time
        try:
            self.cooldown_time = max(1, int(duration_sec))
            await self._enter_cooldown(deployment_id, reason, 0)
        finally:
            self.cooldown_time = original


class CooldownRecoveryMonitor:
    def __init__(
        self,
        state_backend: DeploymentStateBackend,
        deployment_ids_provider: Callable[[], Awaitable[list[str]]],
        check_interval: int = 30,
    ):
        self.state = state_backend
        self.deployment_ids_provider = deployment_ids_provider
        self.check_interval = check_interval
        self._running = False

    async def start_monitoring(self) -> None:
        self._running = True
        while self._running:
            await self._check_recoveries()
            await __import__("asyncio").sleep(self.check_interval)

    def stop(self) -> None:
        self._running = False

    async def _check_recoveries(self) -> None:
        deployment_ids = await self.deployment_ids_provider()
        for deployment_id in deployment_ids:
            if not await self.state.is_cooled_down(deployment_id):
                await self.state.set_health(deployment_id, True)
                await self.state.record_success(deployment_id)
