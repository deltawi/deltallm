from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Awaitable, Callable

import httpx

from src.metrics import increment_router_health_update_failure
from src.models.errors import (
    GatewayCapacityError,
    InvalidRequestError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    ProxyError,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
    parse_retry_after_header,
)
from src.router.candidates import (
    AttemptPermit,
    DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    RouteCandidatePlanner,
)
from src.router.cooldown import CooldownManager
from src.router.execution import (
    ManagedFailoverResult,
    ProviderAttemptResult,
    RequestDeadline,
    attach_failover_attempt_context,
    attach_failover_original_error,
)
from src.router.health_policy import affects_deployment_health
from src.router.router import Deployment
from src.router.state import DeploymentStateBackend

logger = logging.getLogger(__name__)
_ATTEMPT_PERMIT_CLEANUP_MARGIN_SECONDS = 30

TimeoutForDeployment = Callable[[Deployment], float | int | None]


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
    event_history_size: int = 1000


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


class _AttemptExecutionError(Exception):
    """Carries the owned permit until the attempt outcome is committed."""

    def __init__(self, error: Exception, permit: AttemptPermit) -> None:
        self.error = error
        self.permit = permit
        super().__init__(str(error))


@dataclass(frozen=True, slots=True)
class _NormalizedExecutionError:
    error: Exception
    classification: str
    allow_classified_fallbacks: bool
    retry_source: Exception


class FallbackEventJournal:
    """Bounded event history shared by all routing-runtime generations."""

    def __init__(self, max_size: int = 1000) -> None:
        self._events: deque[FallbackEvent] = deque(maxlen=max(1, int(max_size or 1000)))

    def configure(self, max_size: int) -> None:
        resolved_size = max(1, int(max_size or 1000))
        if self._events.maxlen == resolved_size:
            return
        self._events = deque(self._events, maxlen=resolved_size)

    def append(self, event: FallbackEvent) -> None:
        self._events.append(event)

    def recent(self, limit: int) -> list[FallbackEvent]:
        return list(self._events)[-max(0, int(limit)) :]


class FailoverManager:
    def __init__(
        self,
        config: FallbackConfig,
        candidate_planner: RouteCandidatePlanner,
        state_backend: DeploymentStateBackend,
        cooldown_manager: CooldownManager,
        event_journal: FallbackEventJournal | None = None,
    ):
        self.config = config
        self.candidate_planner = candidate_planner
        self.state = state_backend
        self.cooldown = cooldown_manager
        self.event_journal = event_journal or FallbackEventJournal(config.event_history_size)
        self.event_journal.configure(config.event_history_size)

    def get_recent_fallback_events(self, limit: int = 50) -> list[dict[str, Any]]:
        events = self.event_journal.recent(limit)
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
        self.event_journal.append(event)

        if success:
            logger.info(
                "Fallback succeeded: model_group=%s from=%s to=%s reason=%s",
                model_group,
                from_id,
                to_id,
                reason,
            )
        else:
            logger.warning(
                "Fallback attempt failed: model_group=%s deployment=%s classification=%s error=%s",
                model_group,
                from_id,
                classification,
                error_msg[:200],
            )

    def _compute_backoff(self, attempt: int, error: Exception | None = None) -> float:
        base = self.config.retry_after or 1.0
        delay = base * (self.config.backoff_multiplier**attempt)
        delay = min(delay, self.config.backoff_max)
        if self.config.backoff_jitter:
            delay = delay * (0.5 + random.random() * 0.5)
        retry_after = getattr(error, "retry_after", None)
        if retry_after is not None:
            try:
                delay = max(delay, max(0.0, float(retry_after)))
            except (TypeError, ValueError):
                pass
        return min(delay, self.config.backoff_max)

    @staticmethod
    def _notify_attempt(
        on_attempt: Callable[[Deployment], None] | None, deployment: Deployment
    ) -> None:
        if on_attempt is None:
            return
        try:
            on_attempt(deployment)
        except Exception:
            logger.warning("failover attempt callback failed", exc_info=True)

    @staticmethod
    def _http_error_message(error: httpx.HTTPError) -> str:
        response = getattr(error, "response", None)
        if response is None:
            return str(error)

        try:
            payload = response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            nested_error = payload.get("error")
            if isinstance(nested_error, dict):
                for key in ("message", "detail"):
                    message = nested_error.get(key)
                    if isinstance(message, str) and message.strip():
                        return message.strip()
            if isinstance(nested_error, str) and nested_error.strip():
                return nested_error.strip()
            for key in ("message", "detail"):
                message = payload.get(key)
                if isinstance(message, str) and message.strip():
                    return message.strip()

        body = str(getattr(response, "text", "") or "").strip()
        if body:
            return body

        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            decoded = content.decode("utf-8", errors="replace").strip()
            if decoded:
                return decoded

        return str(error)

    def _normalize_http_error(self, error: httpx.HTTPError) -> ProxyError:
        if isinstance(error, httpx.PoolTimeout):
            return GatewayCapacityError()
        if isinstance(error, httpx.TimeoutException):
            return TimeoutError(
                message=str(error) or None,
                affects_deployment_health=True,
            )
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code == 429:
            return RateLimitError(
                message=self._http_error_message(error),
                retry_after=parse_retry_after_header(response.headers.get("retry-after")),
                affects_deployment_health=True,
            )
        if affects_deployment_health(error):
            return ServiceUnavailableError(
                message=str(error),
                affects_deployment_health=True,
            )
        return InvalidRequestError(
            message=self._http_error_message(error),
            affects_deployment_health=False,
        )

    def _normalize_execution_error(
        self,
        error: Exception,
        deployment: Deployment,
    ) -> _NormalizedExecutionError:
        if isinstance(error, asyncio.TimeoutError):
            normalized = TimeoutError(message=f"Deployment '{deployment.deployment_id}' timed out")
            return _NormalizedExecutionError(
                error=normalized,
                classification=ErrorClassification.TIMEOUT,
                allow_classified_fallbacks=True,
                retry_source=normalized,
            )

        if isinstance(error, ProxyError):
            return _NormalizedExecutionError(
                error=error,
                classification=ErrorClassification.classify(error),
                allow_classified_fallbacks=True,
                retry_source=error,
            )

        if isinstance(error, httpx.HTTPError):
            normalized = self._normalize_http_error(error)
            return _NormalizedExecutionError(
                error=normalized,
                classification=ErrorClassification.classify(normalized),
                allow_classified_fallbacks=True,
                retry_source=normalized,
            )

        normalized = attach_failover_original_error(
            ServiceUnavailableError(
                message=str(error),
                affects_deployment_health=bool(getattr(error, "affects_deployment_health", False)),
            ),
            error,
        )
        return _NormalizedExecutionError(
            error=normalized,
            classification=ErrorClassification.classify(normalized),
            allow_classified_fallbacks=False,
            retry_source=error,
        )

    async def execute_with_failover(
        self,
        primary_deployment: Deployment,
        model_group: str,
        execute: Callable[[Deployment], Awaitable[Any]],
        request_tokens: int = 0,
        *,
        return_deployment: bool = False,
        on_attempt: Callable[[Deployment], None] | None = None,
        timeout_seconds: float | None = None,
        timeout_for_deployment: TimeoutForDeployment | None = None,
        retry_max_attempts: int | None = None,
        retryable_error_classes: list[str] | set[str] | None = None,
        routing_context: dict[str, Any] | None = None,
        request_deadline: RequestDeadline | None = None,
        _manage_attempt_lifecycle: bool = False,
    ) -> Any:
        attempt_history: list[str] = []
        try:
            return await self._execute_with_failover(
                primary_deployment=primary_deployment,
                model_group=model_group,
                execute=execute,
                request_tokens=request_tokens,
                return_deployment=return_deployment,
                on_attempt=on_attempt,
                timeout_seconds=timeout_seconds,
                timeout_for_deployment=timeout_for_deployment,
                retry_max_attempts=retry_max_attempts,
                retryable_error_classes=retryable_error_classes,
                routing_context=routing_context,
                request_deadline=request_deadline,
                _manage_attempt_lifecycle=_manage_attempt_lifecycle,
                _attempt_history=attempt_history,
            )
        except Exception as exc:
            attach_failover_attempt_context(
                exc,
                model_group=model_group,
                attempted_deployment_ids=attempt_history,
            )
            raise

    async def _execute_with_failover(
        self,
        primary_deployment: Deployment,
        model_group: str,
        execute: Callable[[Deployment], Awaitable[Any]],
        request_tokens: int = 0,
        *,
        return_deployment: bool = False,
        on_attempt: Callable[[Deployment], None] | None = None,
        timeout_seconds: float | None = None,
        timeout_for_deployment: TimeoutForDeployment | None = None,
        retry_max_attempts: int | None = None,
        retryable_error_classes: list[str] | set[str] | None = None,
        routing_context: dict[str, Any] | None = None,
        request_deadline: RequestDeadline | None = None,
        _manage_attempt_lifecycle: bool = False,
        _attempt_history: list[str],
    ) -> Any:
        routing_context = routing_context if routing_context is not None else {}
        deadline = request_deadline or self.create_request_deadline(timeout_seconds)
        chain = await deadline.wait_for(
            self._build_fallback_chain(
                primary_deployment,
                model_group,
                request_tokens,
                routing_context,
            )
        )
        effective_retries = self._effective_retry_count(retry_max_attempts)
        effective_retry_classes = self._normalize_retryable_error_classes(retryable_error_classes)
        last_error: Exception | None = None
        previous_deployment_id = primary_deployment.deployment_id
        visited_ids: set[str] = set()
        attempted_ids: set[str] = set()
        attempt_history = _attempt_history
        retries_used = 0

        for chain_index, deployment in enumerate(chain):
            if deployment.deployment_id in visited_ids:
                continue
            visited_ids.add(deployment.deployment_id)
            deployment_was_attempted = False
            attempt = 0
            while True:
                try:
                    attempted, result, permit = await self._execute_attempt(
                        deployment,
                        execute,
                        routing_context,
                        attempted_ids,
                        attempt_history,
                        deadline,
                        on_attempt=on_attempt,
                        timeout_seconds=timeout_seconds,
                        timeout_for_deployment=timeout_for_deployment,
                        defer_success=_manage_attempt_lifecycle,
                    )
                    if not attempted:
                        break
                    deployment_was_attempted = True
                    try:
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

                        if _manage_attempt_lifecycle:
                            managed = ManagedFailoverResult(
                                value=result,
                                deployment=deployment,
                                deadline=deadline,
                                _release=partial(self._release_attempt, permit, deployment),
                                recovery_token=permit.owner_token if permit.recovery else None,
                            )
                            permit = None
                            return managed
                        await self._release_attempt(permit, deployment)
                        permit = None
                        if return_deployment:
                            return result, deployment
                        return result
                    finally:
                        if permit is not None:
                            await self._release_attempt(permit, deployment)
                except Exception as caught_exc:
                    attempt_failure = (
                        caught_exc if isinstance(caught_exc, _AttemptExecutionError) else None
                    )
                    exc = attempt_failure.error if attempt_failure is not None else caught_exc
                    deployment_was_attempted = deployment.deployment_id in attempted_ids
                    normalized = self._normalize_execution_error(exc, deployment)
                    last_error = normalized.error
                    classification = normalized.classification
                    error_message = str(last_error)
                    reason = (
                        "timeout"
                        if classification == ErrorClassification.TIMEOUT
                        else classification
                    )
                    try:
                        entered_cooldown = await self.cooldown.record_failure(
                            deployment.health_ref,
                            error_message,
                            exc=last_error,
                            recovery_token=self._recovery_token(attempt_failure),
                        )
                    finally:
                        if attempt_failure is not None:
                            await self._release_attempt(attempt_failure.permit, deployment)

                    self._record_fallback_event(
                        model_group=model_group,
                        from_id=deployment.deployment_id,
                        to_id=None,
                        reason=reason,
                        classification=classification,
                        error_msg=error_message,
                        attempt=attempt,
                        success=False,
                    )

                    if normalized.allow_classified_fallbacks:
                        extra_chain = await deadline.wait_for(
                            self._get_classified_fallbacks(
                                classification,
                                model_group,
                                routing_context,
                                excluded_ids=visited_ids | attempted_ids,
                            )
                        )
                        if extra_chain:
                            extra_result = await self._try_classified_fallbacks(
                                extra_chain,
                                model_group,
                                execute,
                                deployment.deployment_id,
                                classification,
                                routing_context,
                                visited_ids,
                                attempted_ids,
                                attempt_history,
                                deadline,
                                on_attempt=on_attempt,
                                timeout_seconds=timeout_seconds,
                                timeout_for_deployment=timeout_for_deployment,
                                defer_success=_manage_attempt_lifecycle,
                            )
                            if extra_result is not None:
                                result, served, permit = extra_result
                                if _manage_attempt_lifecycle:
                                    managed = ManagedFailoverResult(
                                        value=result,
                                        deployment=served,
                                        deadline=deadline,
                                        _release=partial(self._release_attempt, permit, served),
                                        recovery_token=(
                                            permit.owner_token if permit.recovery else None
                                        ),
                                    )
                                    permit = None
                                    return managed
                                await self._release_attempt(permit, served)
                                if return_deployment:
                                    return result, served
                                return result

                    if not affects_deployment_health(last_error):
                        attach_failover_attempt_context(
                            last_error,
                            model_group=model_group,
                            attempted_deployment_ids=attempt_history,
                        )
                        if last_error is exc:
                            raise last_error
                        raise last_error from exc

                    if (
                        not entered_cooldown
                        and self._should_retry(
                            classification,
                            normalized.retry_source,
                            effective_retry_classes,
                        )
                        and retries_used < effective_retries
                    ):
                        delay = self._compute_backoff(retries_used, last_error)
                        retries_used += 1
                        await deadline.wait_for(asyncio.sleep(delay))
                        attempt += 1
                        continue
                    break

            if deployment_was_attempted:
                previous_deployment_id = deployment.deployment_id

        if isinstance(last_error, ProxyError):
            attach_failover_attempt_context(
                last_error,
                model_group=model_group,
                attempted_deployment_ids=attempt_history,
            )
            raise last_error
        if last_error is not None:
            final_error = ServiceUnavailableError(
                message=f"All deployments exhausted: {last_error}"
            )
            raise attach_failover_attempt_context(
                final_error,
                model_group=model_group,
                attempted_deployment_ids=attempt_history,
            )
        final_error = ServiceUnavailableError(
            message="No healthy deployments available",
            code=NO_HEALTHY_DEPLOYMENTS_CODE,
        )
        raise attach_failover_attempt_context(
            final_error,
            model_group=model_group,
            attempted_deployment_ids=attempt_history,
        )

    def create_request_deadline(self, timeout_seconds: float | None = None) -> RequestDeadline:
        return RequestDeadline.after(self._effective_timeout(timeout_seconds))

    async def execute_managed_with_failover(
        self,
        primary_deployment: Deployment,
        model_group: str,
        execute: Callable[[Deployment], Awaitable[Any]],
        request_tokens: int = 0,
        *,
        on_attempt: Callable[[Deployment], None] | None = None,
        timeout_seconds: float | None = None,
        timeout_for_deployment: TimeoutForDeployment | None = None,
        retry_max_attempts: int | None = None,
        retryable_error_classes: list[str] | set[str] | None = None,
        routing_context: dict[str, Any] | None = None,
    ) -> ManagedFailoverResult[Any]:
        return await self.execute_with_failover(
            primary_deployment=primary_deployment,
            model_group=model_group,
            execute=execute,
            request_tokens=request_tokens,
            on_attempt=on_attempt,
            timeout_seconds=timeout_seconds,
            timeout_for_deployment=timeout_for_deployment,
            retry_max_attempts=retry_max_attempts,
            retryable_error_classes=retryable_error_classes,
            routing_context=routing_context,
            _manage_attempt_lifecycle=True,
        )

    async def _get_classified_fallbacks(
        self,
        classification: str,
        model_group: str,
        routing_context: dict[str, Any],
        *,
        excluded_ids: set[str],
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

        plans = await self.candidate_planner.plan_deployments(
            fallback_groups,
            routing_context,
        )
        chain: list[Deployment] = []
        seen = set(excluded_ids)
        for group in fallback_groups:
            plan = plans.get(group)
            if plan is None:
                continue
            for dep in plan.deployments:
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
        routing_context: dict[str, Any],
        visited_ids: set[str],
        attempted_ids: set[str],
        attempt_history: list[str],
        deadline: RequestDeadline,
        *,
        on_attempt: Callable[[Deployment], None] | None = None,
        timeout_seconds: float | None,
        timeout_for_deployment: TimeoutForDeployment | None = None,
        defer_success: bool = False,
    ) -> tuple[Any, Deployment, AttemptPermit] | None:
        for deployment in chain:
            if deployment.deployment_id in visited_ids:
                continue
            visited_ids.add(deployment.deployment_id)
            try:
                attempted, result, permit = await self._execute_attempt(
                    deployment,
                    execute,
                    routing_context,
                    attempted_ids,
                    attempt_history,
                    deadline,
                    on_attempt=on_attempt,
                    timeout_seconds=timeout_seconds,
                    timeout_for_deployment=timeout_for_deployment,
                    defer_success=defer_success,
                )
                if not attempted:
                    continue
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

                return result, deployment, permit
            except Exception as caught_exc:
                attempt_failure = (
                    caught_exc if isinstance(caught_exc, _AttemptExecutionError) else None
                )
                exc = attempt_failure.error if attempt_failure is not None else caught_exc
                normalized = self._normalize_execution_error(exc, deployment)
                normalized_error = normalized.error
                failure_classification = normalized.classification
                error_message = str(normalized_error)
                try:
                    await self.cooldown.record_failure(
                        deployment.health_ref,
                        error_message,
                        exc=normalized_error,
                        recovery_token=self._recovery_token(attempt_failure),
                    )
                finally:
                    if attempt_failure is not None:
                        await self._release_attempt(attempt_failure.permit, deployment)
                self._record_fallback_event(
                    model_group=model_group,
                    from_id=from_deployment_id,
                    to_id=deployment.deployment_id,
                    reason=classification,
                    classification=failure_classification,
                    error_msg=error_message,
                    attempt=0,
                    success=False,
                )
                if not normalized.allow_classified_fallbacks:
                    attach_failover_attempt_context(
                        normalized_error,
                        model_group=model_group,
                        attempted_deployment_ids=attempt_history,
                    )
                    raise normalized_error from exc
                if not affects_deployment_health(
                    normalized_error
                ) and failure_classification not in {
                    ErrorClassification.CONTEXT_WINDOW,
                    ErrorClassification.CONTENT_POLICY,
                }:
                    if normalized_error is exc:
                        attach_failover_attempt_context(
                            normalized_error,
                            model_group=model_group,
                            attempted_deployment_ids=attempt_history,
                        )
                        raise
                    attach_failover_attempt_context(
                        normalized_error,
                        model_group=model_group,
                        attempted_deployment_ids=attempt_history,
                    )
                    raise normalized_error from exc

        return None

    async def _execute_attempt(
        self,
        deployment: Deployment,
        execute: Callable[[Deployment], Awaitable[Any]],
        routing_context: dict[str, Any],
        attempted_ids: set[str],
        attempt_history: list[str],
        deadline: RequestDeadline,
        *,
        on_attempt: Callable[[Deployment], None] | None,
        timeout_seconds: float | None,
        timeout_for_deployment: TimeoutForDeployment | None,
        defer_success: bool = False,
    ) -> tuple[bool, Any, AttemptPermit | None]:
        attempt_timeout = self._effective_attempt_timeout(
            deployment,
            timeout_seconds,
            timeout_for_deployment,
        )
        attempt_timeout = min(attempt_timeout, deadline.require_remaining())
        permit = await deadline.wait_for(
            self.candidate_planner.acquire_attempt(
                deployment,
                routing_context,
                lease_ttl_seconds=self._attempt_permit_ttl_seconds(attempt_timeout),
            )
        )
        if not permit.acquired:
            return False, None, None

        attempted_ids.add(deployment.deployment_id)
        attempt_history.append(deployment.deployment_id)
        try:
            self._notify_attempt(on_attempt, deployment)
            started = time.monotonic()
            result = await deadline.wait_for(execute(deployment), limit=attempt_timeout)
            latency_ms = (time.monotonic() - started) * 1000
            await self._record_attempt_latency(deployment, latency_ms)
            if isinstance(result, ProviderAttemptResult):
                await self._record_aggregate_health_failure(
                    deployment,
                    result.health_error,
                    permit,
                )
                result = result.value
            elif not defer_success:
                await self._record_attempt_success(deployment, permit)
            return True, result, permit
        except Exception as exc:
            raise _AttemptExecutionError(exc, permit) from exc
        except BaseException:
            await self._release_attempt(permit, deployment)
            raise

    async def _record_aggregate_health_failure(
        self,
        deployment: Deployment,
        error: Exception,
        permit: AttemptPermit,
    ) -> None:
        try:
            await self.cooldown.record_failure(
                deployment.health_ref,
                str(error),
                exc=error,
                recovery_token=permit.owner_token if permit.recovery else None,
            )
        except Exception:
            # A mixed provider result may already contain successful item effects,
            # so routing-state reporting cannot make it safe to replay the attempt.
            increment_router_health_update_failure()
            logger.warning(
                "aggregate provider health update failed deployment_id=%s",
                deployment.deployment_id,
                exc_info=True,
            )

    async def _record_attempt_latency(
        self,
        deployment: Deployment,
        latency_ms: float,
    ) -> None:
        try:
            await self.state.record_latency(deployment.deployment_id, latency_ms)
        except Exception:
            logger.warning(
                "router attempt latency update failed deployment_id=%s",
                deployment.deployment_id,
                exc_info=True,
            )

    async def _record_attempt_success(
        self,
        deployment: Deployment,
        permit: AttemptPermit,
    ) -> None:
        try:
            await self.cooldown.record_success(
                deployment.health_ref,
                recovery_token=permit.owner_token if permit.recovery else None,
            )
        except Exception:
            increment_router_health_update_failure()
            logger.warning(
                "post-provider router health update failed deployment_id=%s",
                deployment.deployment_id,
                exc_info=True,
            )

    @staticmethod
    def _recovery_token(attempt_failure: _AttemptExecutionError | None) -> str | None:
        if attempt_failure is None or not attempt_failure.permit.recovery:
            return None
        return attempt_failure.permit.owner_token

    async def _release_attempt(
        self,
        permit: AttemptPermit,
        deployment: Deployment,
    ) -> None:
        try:
            await self.candidate_planner.release_attempt(permit)
        except Exception:
            # Cleanup failure is local routing-state degradation. The expiring,
            # owner-scoped permit bounds the stale count, and a provider result or
            # provider error must never be replaced or retried because release failed.
            logger.warning(
                "router attempt permit release failed deployment_id=%s",
                deployment.deployment_id,
                exc_info=True,
            )

    @staticmethod
    def _attempt_permit_ttl_seconds(attempt_timeout: float) -> int:
        if not math.isfinite(attempt_timeout):
            return DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS
        return max(1, math.ceil(attempt_timeout) + _ATTEMPT_PERMIT_CLEANUP_MARGIN_SECONDS)

    @staticmethod
    def _coerce_positive_timeout(timeout_seconds: Any) -> float | None:
        if timeout_seconds is None:
            return None
        try:
            parsed = float(timeout_seconds)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _effective_timeout(self, timeout_seconds: float | None) -> float:
        return self._coerce_positive_timeout(timeout_seconds) or self.config.timeout

    def _effective_attempt_timeout(
        self,
        deployment: Deployment,
        timeout_seconds: float | None,
        timeout_for_deployment: TimeoutForDeployment | None,
    ) -> float:
        if timeout_for_deployment is None:
            return self._effective_timeout(timeout_seconds)
        try:
            deployment_timeout = timeout_for_deployment(deployment)
        except Exception:
            logger.debug(
                "Failed to resolve timeout for deployment %s",
                deployment.deployment_id,
                exc_info=True,
            )
            return self._effective_timeout(timeout_seconds)
        if deployment_timeout is None:
            return self._effective_timeout(timeout_seconds)
        return self._coerce_positive_timeout(deployment_timeout) or self._effective_timeout(
            timeout_seconds
        )

    def _effective_retry_count(self, retry_max_attempts: int | None) -> int:
        if retry_max_attempts is None:
            return max(0, int(self.config.num_retries))
        try:
            parsed = int(retry_max_attempts)
        except (TypeError, ValueError):
            return max(0, int(self.config.num_retries))
        return max(0, parsed)

    @staticmethod
    def _normalize_retryable_error_classes(
        retryable_error_classes: list[str] | set[str] | None,
    ) -> set[str] | None:
        if retryable_error_classes is None:
            return None
        normalized = {
            str(item).strip().lower() for item in retryable_error_classes if str(item).strip()
        }
        return normalized or None

    @staticmethod
    def _should_retry(
        classification: str,
        error: Exception,
        retryable_error_classes: set[str] | None,
    ) -> bool:
        if retryable_error_classes is None:
            return RetryPolicy.is_retryable(error)
        return classification.lower() in retryable_error_classes

    async def _build_fallback_chain(
        self,
        primary_deployment: Deployment,
        model_group: str,
        request_tokens: int,
        routing_context: dict[str, Any],
    ) -> list[Deployment]:
        del request_tokens

        fallback_groups = self.config.fallbacks.get(model_group, [])
        groups = [model_group, *fallback_groups]
        plans = await self.candidate_planner.plan_deployments(groups, routing_context)
        chain: list[Deployment] = []
        seen: set[str] = set()

        def add(deployments: list[Deployment]) -> None:
            for deployment in deployments:
                if deployment.deployment_id in seen:
                    continue
                chain.append(deployment)
                seen.add(deployment.deployment_id)

        eligible_ids = {
            deployment.deployment_id for plan in plans.values() for deployment in plan.deployments
        }
        if primary_deployment.deployment_id in eligible_ids:
            add([primary_deployment])

        for group in groups:
            plan = plans.get(group)
            if plan is not None:
                add(list(plan.deployments))

        return chain
