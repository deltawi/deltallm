from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from src.models.errors import InvalidRequestError, ProxyError, ServiceUnavailableError, TimeoutError
from src.router.cooldown import CooldownManager
from src.router.router import Deployment
from src.router.state import DeploymentStateBackend

logger = logging.getLogger(__name__)


@dataclass
class FallbackConfig:
    num_retries: int = 0
    retry_after: float = 0.0
    timeout: float = 600.0
    fallbacks: dict[str, list[str]] = field(default_factory=dict)
    context_window_fallbacks: dict[str, list[str]] = field(default_factory=dict)
    content_policy_fallbacks: dict[str, list[str]] = field(default_factory=dict)
    backoff_multiplier: float = 2.0
    backoff_max: float = 30.0
    backoff_jitter: bool = True


class ErrorClassification:
    CONTEXT_WINDOW = "context_window_exceeded"
    CONTENT_POLICY = "content_policy_violation"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    GENERIC = "generic"

    _CONTEXT_WINDOW_PATTERNS = [
        "context_length_exceeded",
        "context window",
        "maximum context length",
        "max_tokens",
        "token limit",
        "too many tokens",
        "input is too long",
        "maximum allowed length",
        "request too large",
    ]

    _CONTENT_POLICY_PATTERNS = [
        "content_policy_violation",
        "content_filter",
        "content management policy",
        "safety system",
        "harmful content",
        "violates our usage policies",
        "flagged by our content filter",
        "responsible ai policy",
    ]

    @classmethod
    def classify(cls, error: Exception) -> str:
        if isinstance(error, (httpx.TimeoutException, TimeoutError)):
            return cls.TIMEOUT

        status_code = getattr(error, "status_code", None)
        if status_code == 429 or getattr(error, "error_type", None) == "rate_limit_error":
            return cls.RATE_LIMIT

        error_body = cls._extract_error_body(error)
        error_text = error_body.lower() if error_body else str(error).lower()

        for pattern in cls._CONTEXT_WINDOW_PATTERNS:
            if pattern in error_text:
                return cls.CONTEXT_WINDOW

        for pattern in cls._CONTENT_POLICY_PATTERNS:
            if pattern in error_text:
                return cls.CONTENT_POLICY

        return cls.GENERIC

    @classmethod
    def _extract_error_body(cls, error: Exception) -> str | None:
        response = getattr(error, "response", None)
        if response is None:
            return None
        if hasattr(response, "text"):
            try:
                return str(response.text)
            except Exception:
                pass
        if hasattr(response, "content"):
            try:
                return response.content.decode("utf-8", errors="replace")
            except Exception:
                pass
        return None


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


@dataclass
class FallbackEvent:
    timestamp: float
    model_group: str
    from_deployment_id: str
    to_deployment_id: str | None
    reason: str
    error_classification: str
    error_message: str
    attempt: int
    success: bool


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
        self._fallback_events: list[FallbackEvent] = []
        self._max_events = 1000

    def get_recent_fallback_events(self, limit: int = 50) -> list[dict[str, Any]]:
        events = self._fallback_events[-limit:]
        return [
            {
                "timestamp": e.timestamp,
                "model_group": e.model_group,
                "from_deployment": e.from_deployment_id,
                "to_deployment": e.to_deployment_id,
                "reason": e.reason,
                "error_classification": e.error_classification,
                "error_message": e.error_message[:200],
                "attempt": e.attempt,
                "success": e.success,
            }
            for e in events
        ]

    def _record_fallback_event(
        self,
        model_group: str,
        from_id: str,
        to_id: str | None,
        reason: str,
        classification: str,
        error_msg: str,
        attempt: int,
        success: bool,
    ) -> None:
        event = FallbackEvent(
            timestamp=time.time(),
            model_group=model_group,
            from_deployment_id=from_id,
            to_deployment_id=to_id,
            reason=reason,
            error_classification=classification,
            error_message=error_msg,
            attempt=attempt,
            success=success,
        )
        self._fallback_events.append(event)
        if len(self._fallback_events) > self._max_events:
            self._fallback_events = self._fallback_events[-self._max_events:]

        if success:
            logger.info(
                "Fallback succeeded: model_group=%s from=%s to=%s reason=%s",
                model_group, from_id, to_id, reason,
            )
        else:
            logger.warning(
                "Fallback attempt failed: model_group=%s deployment=%s classification=%s error=%s",
                model_group, from_id, classification, error_msg[:200],
            )

    def _compute_backoff(self, attempt: int) -> float:
        base = self.config.retry_after or 1.0
        delay = base * (self.config.backoff_multiplier ** attempt)
        delay = min(delay, self.config.backoff_max)
        if self.config.backoff_jitter:
            delay = delay * (0.5 + random.random() * 0.5)
        return delay

    async def execute_with_failover(
        self,
        primary_deployment: Deployment,
        model_group: str,
        execute: Callable[[Deployment], Awaitable[Any]],
        request_tokens: int = 0,
    ) -> Any:
        chain = self._build_fallback_chain(primary_deployment, model_group, request_tokens)
        last_error: Exception | None = None
        previous_deployment_id = primary_deployment.deployment_id

        for chain_index, deployment in enumerate(chain):
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

                    if chain_index > 0:
                        self._record_fallback_event(
                            model_group=model_group,
                            from_id=previous_deployment_id,
                            to_id=deployment.deployment_id,
                            reason="primary_failed",
                            classification="success",
                            error_msg="",
                            attempt=attempt,
                            success=True,
                        )

                    return result
                except asyncio.TimeoutError:
                    last_error = TimeoutError(message=f"Deployment '{deployment.deployment_id}' timed out")
                    classification = ErrorClassification.TIMEOUT
                    await self.cooldown.record_failure(deployment.deployment_id, "timeout")

                    self._record_fallback_event(
                        model_group=model_group,
                        from_id=deployment.deployment_id,
                        to_id=None,
                        reason="timeout",
                        classification=classification,
                        error_msg=str(last_error),
                        attempt=attempt,
                        success=False,
                    )

                    if attempt < self.config.num_retries:
                        await asyncio.sleep(self._compute_backoff(attempt))
                except ProxyError as exc:
                    last_error = exc
                    classification = ErrorClassification.classify(exc)
                    await self.cooldown.record_failure(deployment.deployment_id, str(exc))

                    self._record_fallback_event(
                        model_group=model_group,
                        from_id=deployment.deployment_id,
                        to_id=None,
                        reason=classification,
                        classification=classification,
                        error_msg=str(exc),
                        attempt=attempt,
                        success=False,
                    )

                    extra_chain = self._get_classified_fallbacks(classification, model_group)
                    if extra_chain:
                        extra_result = await self._try_classified_fallbacks(
                            extra_chain, model_group, execute, deployment.deployment_id, classification,
                        )
                        if extra_result is not None:
                            return extra_result

                    if RetryPolicy.is_retryable(exc) and attempt < self.config.num_retries:
                        await asyncio.sleep(self._compute_backoff(attempt))
                        continue
                    break
                except httpx.HTTPError as exc:
                    classification = ErrorClassification.classify(exc)
                    last_error = ServiceUnavailableError(message=str(exc))
                    await self.cooldown.record_failure(deployment.deployment_id, str(exc))

                    self._record_fallback_event(
                        model_group=model_group,
                        from_id=deployment.deployment_id,
                        to_id=None,
                        reason=classification,
                        classification=classification,
                        error_msg=str(exc),
                        attempt=attempt,
                        success=False,
                    )

                    extra_chain = self._get_classified_fallbacks(classification, model_group)
                    if extra_chain:
                        extra_result = await self._try_classified_fallbacks(
                            extra_chain, model_group, execute, deployment.deployment_id, classification,
                        )
                        if extra_result is not None:
                            return extra_result

                    if RetryPolicy.is_retryable(exc) and attempt < self.config.num_retries:
                        await asyncio.sleep(self._compute_backoff(attempt))
                        continue
                    break
                except Exception as exc:
                    classification = ErrorClassification.classify(exc)
                    last_error = ServiceUnavailableError(message=str(exc))
                    await self.cooldown.record_failure(deployment.deployment_id, str(exc))

                    self._record_fallback_event(
                        model_group=model_group,
                        from_id=deployment.deployment_id,
                        to_id=None,
                        reason=classification,
                        classification=classification,
                        error_msg=str(exc),
                        attempt=attempt,
                        success=False,
                    )

                    extra_chain = self._get_classified_fallbacks(classification, model_group)
                    if extra_chain:
                        extra_result = await self._try_classified_fallbacks(
                            extra_chain, model_group, execute, deployment.deployment_id, classification,
                        )
                        if extra_result is not None:
                            return extra_result

                    if RetryPolicy.is_retryable(exc) and attempt < self.config.num_retries:
                        await asyncio.sleep(self._compute_backoff(attempt))
                        continue
                    break
                finally:
                    await self.state.decrement_active(deployment.deployment_id)

            previous_deployment_id = deployment.deployment_id

        if isinstance(last_error, ProxyError):
            raise last_error
        if last_error is not None:
            raise ServiceUnavailableError(message=f"All deployments exhausted: {last_error}")
        raise ServiceUnavailableError(message="No healthy deployments available")

    def _get_classified_fallbacks(
        self,
        classification: str,
        model_group: str,
    ) -> list[Deployment]:
        if classification == ErrorClassification.CONTEXT_WINDOW:
            fallback_map = self.config.context_window_fallbacks
        elif classification == ErrorClassification.CONTENT_POLICY:
            fallback_map = self.config.content_policy_fallbacks
        else:
            return []

        fallback_groups = fallback_map.get(model_group, [])
        if not fallback_groups:
            return []

        chain: list[Deployment] = []
        seen: set[str] = set()
        for group in fallback_groups:
            for dep in self.registry.get(group, []):
                if dep.deployment_id not in seen:
                    chain.append(dep)
                    seen.add(dep.deployment_id)
        return chain

    async def _try_classified_fallbacks(
        self,
        chain: list[Deployment],
        model_group: str,
        execute: Callable[[Deployment], Awaitable[Any]],
        from_deployment_id: str,
        classification: str,
    ) -> Any | None:
        for deployment in chain:
            if await self.state.is_cooled_down(deployment.deployment_id):
                continue
            health = await self.state.get_health(deployment.deployment_id)
            if health.get("healthy", "true") == "false":
                continue

            try:
                await self.state.increment_active(deployment.deployment_id)
                started = time.monotonic()
                result = await asyncio.wait_for(
                    execute(deployment),
                    timeout=self.config.timeout,
                )
                latency_ms = (time.monotonic() - started) * 1000
                await self.state.record_latency(deployment.deployment_id, latency_ms)
                await self.cooldown.record_success(deployment.deployment_id)

                self._record_fallback_event(
                    model_group=model_group,
                    from_id=from_deployment_id,
                    to_id=deployment.deployment_id,
                    reason=classification,
                    classification=classification,
                    error_msg="",
                    attempt=0,
                    success=True,
                )

                return result
            except Exception as exc:
                await self.cooldown.record_failure(deployment.deployment_id, str(exc))
                self._record_fallback_event(
                    model_group=model_group,
                    from_id=from_deployment_id,
                    to_id=deployment.deployment_id,
                    reason=classification,
                    classification=classification,
                    error_msg=str(exc),
                    attempt=0,
                    success=False,
                )
            finally:
                await self.state.decrement_active(deployment.deployment_id)

        return None

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
