from __future__ import annotations

import httpx
import pytest

from src.batch.retry import BatchResponseShapeError, BatchRetryCategory, classify_batch_retry
from src.models.errors import (
    BudgetExceededError,
    InvalidRequestError,
    ModelNotFoundError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
)


def _http_status_error(status_code: int, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.com/v1/embeddings")
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    response = httpx.Response(status_code, headers=headers, request=request)
    return httpx.HTTPStatusError("upstream failed", request=request, response=response)


@pytest.mark.parametrize(
    ("exc", "category", "retryable", "retry_after"),
    [
        (RateLimitError(message="rate limited", retry_after=30), BatchRetryCategory.RATE_LIMIT, True, 30),
        (_http_status_error(429, retry_after="17"), BatchRetryCategory.RATE_LIMIT, True, 17),
        (_http_status_error(408), BatchRetryCategory.TIMEOUT, True, None),
        (_http_status_error(500), BatchRetryCategory.UPSTREAM_5XX, True, None),
        (_http_status_error(504), BatchRetryCategory.UPSTREAM_5XX, True, None),
        (httpx.ReadTimeout("slow upstream"), BatchRetryCategory.TIMEOUT, True, None),
        (httpx.ReadError("connection reset"), BatchRetryCategory.TRANSPORT, True, None),
        (TimeoutError(message="request timeout"), BatchRetryCategory.TIMEOUT, True, None),
        (ServiceUnavailableError(message="overloaded"), BatchRetryCategory.SERVICE_UNAVAILABLE, True, None),
        (
            ServiceUnavailableError(
                message="No healthy deployments available for model 'text-embedding-3-small'",
                code=NO_HEALTHY_DEPLOYMENTS_CODE,
            ),
            BatchRetryCategory.NO_HEALTHY_DEPLOYMENTS,
            True,
            None,
        ),
        (
            ModelNotFoundError(message="No healthy deployments available for model 'text-embedding-3-small'"),
            BatchRetryCategory.NO_HEALTHY_DEPLOYMENTS,
            True,
            None,
        ),
        (BudgetExceededError(message="Budget exceeded"), BatchRetryCategory.BUDGET, False, None),
        (InvalidRequestError(message="bad request"), BatchRetryCategory.INVALID_REQUEST, False, None),
        (PermissionDeniedError(message="forbidden"), BatchRetryCategory.PERMISSION, False, None),
        (ModelNotFoundError(message="Model 'missing' is not configured"), BatchRetryCategory.MISSING_MODEL, False, None),
        (BatchResponseShapeError("microbatch response length mismatch"), BatchRetryCategory.RESPONSE_SHAPE, False, None),
        (RuntimeError("local bug"), BatchRetryCategory.UNKNOWN, False, None),
    ],
)
def test_classify_batch_retry_matrix(
    exc: Exception,
    category: BatchRetryCategory,
    retryable: bool,
    retry_after: int | None,
) -> None:
    decision = classify_batch_retry(exc)

    assert decision.category is category
    assert decision.retryable is retryable
    assert decision.retry_after_seconds == retry_after
    if not retryable:
        assert decision.terminal_reason == "not_retryable"


def test_classify_batch_retry_treats_http_400_as_terminal_invalid_request() -> None:
    decision = classify_batch_retry(_http_status_error(400))

    assert decision.retryable is False
    assert decision.category is BatchRetryCategory.INVALID_REQUEST
    assert decision.terminal_reason == "not_retryable"


def test_classify_batch_retry_does_not_treat_plain_value_error_as_response_shape() -> None:
    decision = classify_batch_retry(ValueError("local request validation failed"))

    assert decision.retryable is False
    assert decision.category is BatchRetryCategory.UNKNOWN
    assert decision.terminal_reason == "not_retryable"
