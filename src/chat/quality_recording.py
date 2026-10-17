from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from time import perf_counter
from typing import Any, Final, TypeVar

from src.chat.response_validation import ResponseValidationError, ResponseValidationReason
from src.router.execution import ProviderAttemptSuccess
from src.router.health_policy import affects_deployment_health
from src.router.quality import DeploymentQualityTracker, QualityOutcome
from src.router.router import Deployment

_T = TypeVar("_T")

_DEFECT_OUTCOMES: Final = {
    ResponseValidationReason.EMPTY_RESPONSE: QualityOutcome.EMPTY_ANSWER,
    ResponseValidationReason.INVALID_TOOL_ARGUMENTS: QualityOutcome.BAD_TOOL_CALL,
    ResponseValidationReason.UNKNOWN_TOOL_NAME: QualityOutcome.BAD_TOOL_CALL,
    ResponseValidationReason.INVALID_JSON_OUTPUT: QualityOutcome.BAD_JSON,
}


class AttemptQualityRecorder:
    """Record one request's chat attempts into the process quality tracker.

    The failover attempt timeout cancels the attempt in this task, so a cancelled
    attempt is held until the request shows why: another attempt or a failover error
    means the attempt timed out, and a cancellation that reaches the caller means the
    client left. Only a timeout counts against the deployment.
    """

    __slots__ = ("_tracker", "_started", "_interrupted")

    def __init__(self, tracker: DeploymentQualityTracker | None) -> None:
        self._tracker = tracker
        self._started: float | None = None
        self._interrupted: Deployment | None = None

    async def run(self, failover: Awaitable[_T]) -> _T:
        try:
            return await failover
        except Exception:
            self._flush_interrupted()
            raise

    async def attempt(self, deployment: Deployment, attempt: Awaitable[_T]) -> _T:
        """Record a non-streaming attempt; the result passes through unchanged."""

        tracker = self._tracker
        if tracker is None:
            return await attempt
        started = self._begin()
        outcome = await self._watched(tracker, deployment, attempt)
        result: Any = outcome.value if isinstance(outcome, ProviderAttemptSuccess) else outcome
        payload = result[0] if isinstance(result, tuple) and result else None
        usage = payload.get("usage") if isinstance(payload, Mapping) else None
        tracker.record(
            deployment.deployment_id,
            QualityOutcome.OK,
            output_tokens=_completion_tokens(usage),
            output_ms=(perf_counter() - started) * 1000,
        )
        return outcome

    async def opened(self, deployment: Deployment, attempt: Awaitable[_T]) -> _T:
        """Record time to first token; the call itself is counted when the stream ends."""

        tracker = self._tracker
        if tracker is None:
            return await attempt
        started = self._begin()
        opened = await self._watched(tracker, deployment, attempt)
        tracker.record(
            deployment.deployment_id, None, first_token_ms=(perf_counter() - started) * 1000
        )
        return opened

    def finished(
        self,
        deployment: Deployment,
        *,
        provider_failed: bool,
        usage: Mapping[str, Any] | None,
    ) -> None:
        tracker = self._tracker
        if tracker is None:
            return
        if provider_failed:
            tracker.record(deployment.deployment_id, QualityOutcome.PROVIDER_ERROR)
        elif usage is not None:
            started = self._started
            tracker.record(
                deployment.deployment_id,
                QualityOutcome.OK,
                output_tokens=_completion_tokens(usage),
                output_ms=(perf_counter() - started) * 1000 if started is not None else None,
            )

    def _begin(self) -> float:
        # Failover runs attempts one at a time, so a new attempt ends the previous one.
        self._flush_interrupted()
        self._started = started = perf_counter()
        return started

    async def _watched(
        self, tracker: DeploymentQualityTracker, deployment: Deployment, attempt: Awaitable[_T]
    ) -> _T:
        try:
            return await attempt
        except asyncio.CancelledError:
            self._interrupted = deployment
            raise
        except ResponseValidationError as exc:
            tracker.record(deployment.deployment_id, _DEFECT_OUTCOMES[exc.reason])
            raise
        except Exception as exc:
            # A request-side error (bad input, auth, policy) says nothing about the provider.
            if affects_deployment_health(exc):
                tracker.record(deployment.deployment_id, QualityOutcome.PROVIDER_ERROR)
            raise

    def _flush_interrupted(self) -> None:
        interrupted, self._interrupted = self._interrupted, None
        if interrupted is not None and self._tracker is not None:
            self._tracker.record(interrupted.deployment_id, QualityOutcome.PROVIDER_ERROR)


def _completion_tokens(usage: object) -> int | None:
    if not isinstance(usage, Mapping):
        return None
    tokens = usage.get("completion_tokens")
    return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None
