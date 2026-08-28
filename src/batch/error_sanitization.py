from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from src.batch.retry import (
    BatchRetryCategory,
    BatchRetryDecision,
    BatchRetryTerminalReason,
)
from src.models.errors import ProxyError

_PUBLIC_MESSAGES = {
    BatchRetryCategory.AUTHENTICATION: "Provider authentication failed",
    BatchRetryCategory.RATE_LIMIT: "Provider rate limited request",
    BatchRetryCategory.TIMEOUT: "Provider request timed out",
    BatchRetryCategory.TRANSPORT: "Provider unavailable",
    BatchRetryCategory.UPSTREAM_5XX: "Provider unavailable",
    BatchRetryCategory.SERVICE_UNAVAILABLE: "Provider unavailable",
    BatchRetryCategory.NO_HEALTHY_DEPLOYMENTS: "No healthy deployments available",
    BatchRetryCategory.INVALID_REQUEST: "Batch request was rejected",
    BatchRetryCategory.RESPONSE_SHAPE: "Provider returned an invalid response",
    BatchRetryCategory.BUDGET: "Budget exceeded",
    BatchRetryCategory.MISSING_MODEL: "Model not found",
    BatchRetryCategory.PERMISSION: "Permission denied",
    BatchRetryCategory.UNKNOWN: "Batch request failed",
}


def stable_batch_error_message(category: BatchRetryCategory | str | None) -> str:
    """Return a public message without consulting persisted or provider-owned text."""

    try:
        resolved = BatchRetryCategory(str(category))
    except ValueError:
        resolved = BatchRetryCategory.UNKNOWN
    return _PUBLIC_MESSAGES[resolved]


def persisted_batch_error_message(exc: Exception, decision: BatchRetryDecision) -> str:
    """Preserve application failures while sanitizing provider HTTP exceptions."""

    if isinstance(exc, httpx.HTTPError):
        return stable_batch_error_message(decision.category)
    if isinstance(exc, ProxyError):
        return exc.message
    return str(exc)


def sanitize_batch_artifact_error(
    error_body: Mapping[str, Any] | None,
    *,
    cancelled: bool,
) -> dict[str, Any]:
    """Build an allowlisted public error from durable, potentially historical state."""

    if cancelled:
        return {"message": "Batch request cancelled", "type": "BatchItemCancelled"}

    source = error_body or {}
    category = _retry_category(source.get("retry_category"))
    sanitized: dict[str, Any] = {
        "message": stable_batch_error_message(category),
        "type": "BatchItemError",
    }
    _copy_bool(source, sanitized, "retryable")
    _copy_enum(source, sanitized, "retry_category", BatchRetryCategory)
    _copy_enum(source, sanitized, "terminal_reason", BatchRetryTerminalReason)
    for key in ("attempt", "max_attempts", "retry_delay_seconds"):
        _copy_non_negative_int(source, sanitized, key)
    return sanitized


def sanitize_batch_item_error_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    """Sanitize durable error fields before returning a batch item through an API."""

    sanitized_item = dict(item)
    cancelled = str(item.get("status") or "") == "cancelled"
    error_body_value = item.get("error_body")
    error_body = error_body_value if isinstance(error_body_value, Mapping) else None
    retry_category = item.get("_error_retry_category")
    if retry_category is None and error_body is not None:
        retry_category = error_body.get("retry_category")
    safe_error = sanitize_batch_artifact_error(
        {"retry_category": retry_category} if error_body is None else error_body,
        cancelled=cancelled,
    )

    if "_has_error_body" in sanitized_item:
        sanitized_item["error_body"] = safe_error if item.get("_has_error_body") else None
    elif "error_body" in sanitized_item and error_body_value is not None:
        sanitized_item["error_body"] = safe_error
    if sanitized_item.get("last_error") is not None:
        sanitized_item["last_error"] = safe_error["message"]
    sanitized_item.pop("_error_retry_category", None)
    sanitized_item.pop("_has_error_body", None)
    return sanitized_item


def _retry_category(value: object) -> BatchRetryCategory:
    try:
        return BatchRetryCategory(str(value))
    except ValueError:
        return BatchRetryCategory.UNKNOWN


def _copy_bool(source: Mapping[str, Any], target: dict[str, Any], key: str) -> None:
    value = source.get(key)
    if isinstance(value, bool):
        target[key] = value


def _copy_enum(
    source: Mapping[str, Any],
    target: dict[str, Any],
    key: str,
    enum_type: type[BatchRetryCategory] | type[BatchRetryTerminalReason],
) -> None:
    value = source.get(key)
    try:
        target[key] = enum_type(str(value)).value
    except ValueError:
        return


def _copy_non_negative_int(source: Mapping[str, Any], target: dict[str, Any], key: str) -> None:
    value = source.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        target[key] = value
