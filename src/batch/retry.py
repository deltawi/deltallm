from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import httpx

from src.models.errors import (
    BudgetExceededError,
    InvalidRequestError,
    ModelNotFoundError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
    parse_retry_after_header,
)

_NO_HEALTHY_DEPLOYMENTS_MARKER = "no healthy deployments available"


class BatchResponseShapeError(ValueError):
    """Raised when an upstream microbatch response violates the expected contract."""


class BatchRetryCategory(StrEnum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    UPSTREAM_5XX = "upstream_5xx"
    SERVICE_UNAVAILABLE = "service_unavailable"
    NO_HEALTHY_DEPLOYMENTS = "no_healthy_deployments"
    INVALID_REQUEST = "invalid_request"
    RESPONSE_SHAPE = "response_shape"
    BUDGET = "budget"
    MISSING_MODEL = "missing_model"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BatchRetryDecision:
    retryable: bool
    category: BatchRetryCategory
    retry_after_seconds: int | None = None
    terminal_reason: str | None = None


def classify_batch_retry(exc: Exception) -> BatchRetryDecision:
    if isinstance(exc, RateLimitError):
        return BatchRetryDecision(
            retryable=True,
            category=BatchRetryCategory.RATE_LIMIT,
            retry_after_seconds=_proxy_retry_after(exc),
        )

    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return BatchRetryDecision(retryable=True, category=BatchRetryCategory.TIMEOUT)

    if isinstance(exc, httpx.TransportError):
        return BatchRetryDecision(retryable=True, category=BatchRetryCategory.TRANSPORT)

    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status_error(exc)

    if isinstance(exc, ServiceUnavailableError):
        if getattr(exc, "code", None) == NO_HEALTHY_DEPLOYMENTS_CODE:
            return BatchRetryDecision(retryable=True, category=BatchRetryCategory.NO_HEALTHY_DEPLOYMENTS)
        return BatchRetryDecision(retryable=True, category=BatchRetryCategory.SERVICE_UNAVAILABLE)

    if isinstance(exc, BudgetExceededError):
        return BatchRetryDecision(
            retryable=False,
            category=BatchRetryCategory.BUDGET,
            terminal_reason="not_retryable",
        )

    if isinstance(exc, InvalidRequestError):
        return BatchRetryDecision(
            retryable=False,
            category=BatchRetryCategory.INVALID_REQUEST,
            terminal_reason="not_retryable",
        )

    if isinstance(exc, PermissionDeniedError):
        return BatchRetryDecision(
            retryable=False,
            category=BatchRetryCategory.PERMISSION,
            terminal_reason="not_retryable",
        )

    if isinstance(exc, ModelNotFoundError):
        if _has_no_healthy_deployments_message(exc):
            return BatchRetryDecision(retryable=True, category=BatchRetryCategory.NO_HEALTHY_DEPLOYMENTS)
        return BatchRetryDecision(
            retryable=False,
            category=BatchRetryCategory.MISSING_MODEL,
            terminal_reason="not_retryable",
        )

    if isinstance(exc, BatchResponseShapeError):
        return BatchRetryDecision(
            retryable=False,
            category=BatchRetryCategory.RESPONSE_SHAPE,
            terminal_reason="not_retryable",
        )

    return BatchRetryDecision(
        retryable=False,
        category=BatchRetryCategory.UNKNOWN,
        terminal_reason="not_retryable",
    )


def _classify_http_status_error(exc: httpx.HTTPStatusError) -> BatchRetryDecision:
    status_code = exc.response.status_code
    retry_after_seconds = parse_retry_after_header(exc.response.headers.get("Retry-After"))

    if status_code == 429:
        return BatchRetryDecision(
            retryable=True,
            category=BatchRetryCategory.RATE_LIMIT,
            retry_after_seconds=retry_after_seconds,
        )
    if status_code == 408:
        return BatchRetryDecision(
            retryable=True,
            category=BatchRetryCategory.TIMEOUT,
            retry_after_seconds=retry_after_seconds,
        )
    if status_code >= 500:
        return BatchRetryDecision(
            retryable=True,
            category=BatchRetryCategory.UPSTREAM_5XX,
            retry_after_seconds=retry_after_seconds,
        )
    return BatchRetryDecision(
        retryable=False,
        category=BatchRetryCategory.INVALID_REQUEST,
        terminal_reason="not_retryable",
    )


def _proxy_retry_after(exc: RateLimitError) -> int | None:
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None:
        return None
    try:
        return max(0, int(retry_after))
    except (TypeError, ValueError, OverflowError):
        return None


def _has_no_healthy_deployments_message(exc: Exception) -> bool:
    return _NO_HEALTHY_DEPLOYMENTS_MARKER in str(exc).lower()
