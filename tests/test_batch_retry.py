from __future__ import annotations

import httpx
import pytest

from src.batch.backpressure import BatchModelGroupDeferred
from src.batch.chat_batching import normalize_chat_microbatch_results
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
        (
            BatchModelGroupDeferred(
                model_group="group:text-embedding-3-small",
                reason="no_healthy_deployments",
                retry_after_seconds=12,
            ),
            BatchRetryCategory.NO_HEALTHY_DEPLOYMENTS,
            True,
            12,
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


@pytest.mark.parametrize(
    ("raw_error", "category", "retryable", "retry_after"),
    [
        ({"status_code": 429, "message": "limited", "retry_after": 3}, BatchRetryCategory.RATE_LIMIT, True, 3),
        ({"status": 503, "message": "provider unavailable"}, BatchRetryCategory.SERVICE_UNAVAILABLE, True, None),
        ({"status_code": 400, "message": "bad request"}, BatchRetryCategory.INVALID_REQUEST, False, None),
        (
            {"error": {"status_code": 429, "message": "nested limited", "retry_after": 5}},
            BatchRetryCategory.RATE_LIMIT,
            True,
            5,
        ),
        (
            {"error": {"status": 503, "message": "nested unavailable"}},
            BatchRetryCategory.SERVICE_UNAVAILABLE,
            True,
            None,
        ),
    ],
)
def test_chat_microbatch_provider_item_errors_preserve_retry_classification(
    raw_error: dict[str, object],
    category: BatchRetryCategory,
    retryable: bool,
    retry_after: int | None,
) -> None:
    result = normalize_chat_microbatch_results(
        [{"index": 0, "error": raw_error}],
        expected_count=1,
        custom_ids=["item-1"],
    )[0]

    assert result.error is not None
    decision = classify_batch_retry(result.error)
    assert decision.category is category
    assert decision.retryable is retryable
    assert decision.retry_after_seconds == retry_after


def _chat_microbatch_success_result(**identity: object) -> dict[str, object]:
    return {
        **identity,
        "response_body": {
            "id": f"chatcmpl-{identity.get('custom_id', identity.get('index', 'unknown'))}",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
        },
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_chat_microbatch_results_accept_matching_index_and_custom_id() -> None:
    result = normalize_chat_microbatch_results(
        [
            _chat_microbatch_success_result(index=1, custom_id="item-2"),
            _chat_microbatch_success_result(index=0, custom_id="item-1"),
        ],
        expected_count=2,
        custom_ids=["item-1", "item-2"],
    )

    assert [row.index for row in result] == [0, 1]
    assert result[0].response_body is not None
    assert result[0].response_body["id"] == "chatcmpl-item-1"
    assert result[1].response_body is not None
    assert result[1].response_body["id"] == "chatcmpl-item-2"


@pytest.mark.parametrize(
    ("raw_results", "expected_count", "custom_ids", "match"),
    [
        (
            [
                _chat_microbatch_success_result(index=0, custom_id="item-2"),
                _chat_microbatch_success_result(index=1, custom_id="item-1"),
            ],
            2,
            ["item-1", "item-2"],
            "mismatched index and custom_id",
        ),
        (
            [_chat_microbatch_success_result(custom_id="missing")],
            1,
            ["item-1"],
            "unknown custom_id",
        ),
        (
            [_chat_microbatch_success_result(index=True)],
            1,
            ["item-1"],
            "invalid index",
        ),
        (
            [_chat_microbatch_success_result(item_index=0, index=1)],
            1,
            ["item-1"],
            "mismatched item_index and index",
        ),
    ],
)
def test_chat_microbatch_results_reject_ambiguous_or_invalid_identity(
    raw_results: list[dict[str, object]],
    expected_count: int,
    custom_ids: list[str],
    match: str,
) -> None:
    with pytest.raises(BatchResponseShapeError, match=match):
        normalize_chat_microbatch_results(raw_results, expected_count=expected_count, custom_ids=custom_ids)
