from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from src.models.errors import InvalidRequestError, ProxyError, ServiceUnavailableError, TimeoutError
from src.router.cooldown import CooldownManager
from src.router.router import Deployment
from src.router.state import DeploymentStateBackend


@dataclass
class FallbackConfig:
    num_retries: int = 0
    retry_after: float = 0.0
    timeout: float = 600.0
    fallbacks: dict[str, list[str]] = field(default_factory=dict)


class RetryPolicy:
    RETRYABLE_ERROR_TYPES = {
        "timeout_error",
        "rate_limit_error",
        "service_unavailable",
    }

    RETRYABLE_STATUS_CODES = {408, 429, 502, 503, 504}

    @classmethod
    def is_retryable(cls, error: Exception) -> bool:
        if isinstance(error, (httpx.TimeoutException, TimeoutError)):
            return True

        status_code = getattr(error, "status_code", None)
        if status_code in cls.RETRYABLE_STATUS_CODES:
            return True

        error_type = getattr(error, "error_type", None)
        if error_type in cls.RETRYABLE_ERROR_TYPES:
            return True

        if isinstance(error, (InvalidRequestError,)):
            return False

        return False


class FailoverManager:
    def __init__(
        self,
        config: FallbackConfig,
        deployment_registry: dict[str, list[Deployment]],
        state_backend: DeploymentStateBackend,
        cooldown_manager: CooldownManager,
    ):
        self.config = config
        self.registry = deployment_registry
        self.state = state_backend
        self.cooldown = cooldown_manager

    async def execute_with_failover(
        self,
        primary_deployment: Deployment,
        model_group: str,
        execute: Callable[[Deployment], Awaitable[Any]],
        request_tokens: int = 0,
    ) -> Any:
        chain = self._build_fallback_chain(primary_deployment, model_group, request_tokens)
        last_error: Exception | None = None

        for deployment in chain:
            if await self.state.is_cooled_down(deployment.deployment_id):
                continue
            health = await self.state.get_health(deployment.deployment_id)
            if health.get("healthy", "true") == "false":
                continue

            for attempt in range(self.config.num_retries + 1):
                started = time.monotonic()
                try:
                    await self.state.increment_active(deployment.deployment_id)
                    result = await asyncio.wait_for(
                        execute(deployment),
                        timeout=self.config.timeout,
                    )
                    latency_ms = (time.monotonic() - started) * 1000
                    await self.state.record_latency(deployment.deployment_id, latency_ms)
                    await self.cooldown.record_success(deployment.deployment_id)
                    return result
                except asyncio.TimeoutError as exc:
                    last_error = TimeoutError(message=f"Deployment '{deployment.deployment_id}' timed out")
                    await self.cooldown.record_failure(deployment.deployment_id, "timeout")
                    if attempt < self.config.num_retries:
                        await asyncio.sleep(self.config.retry_after)
                except ProxyError as exc:
                    last_error = exc
                    await self.cooldown.record_failure(deployment.deployment_id, str(exc))
                    if RetryPolicy.is_retryable(exc) and attempt < self.config.num_retries:
                        await asyncio.sleep(self.config.retry_after)
                        continue
                    break
                except Exception as exc:
                    last_error = ServiceUnavailableError(message=str(exc))
                    await self.cooldown.record_failure(deployment.deployment_id, str(exc))
                    if RetryPolicy.is_retryable(exc) and attempt < self.config.num_retries:
                        await asyncio.sleep(self.config.retry_after)
                        continue
                    break
                finally:
                    await self.state.decrement_active(deployment.deployment_id)

        if isinstance(last_error, ProxyError):
            raise last_error
        if last_error is not None:
            raise ServiceUnavailableError(message=f"All deployments exhausted: {last_error}")
        raise ServiceUnavailableError(message="No healthy deployments available")

    def _build_fallback_chain(
        self,
        primary_deployment: Deployment,
        model_group: str,
        request_tokens: int,
    ) -> list[Deployment]:
        del request_tokens

        chain: list[Deployment] = []
        seen: set[str] = set()

        def add(deployments: list[Deployment]) -> None:
            for deployment in deployments:
                if deployment.deployment_id in seen:
                    continue
                chain.append(deployment)
                seen.add(deployment.deployment_id)

        add([primary_deployment])
        add(self.registry.get(model_group, []))

        for fallback_group in self.config.fallbacks.get(model_group, []):
            add(self.registry.get(fallback_group, []))

        return chain
