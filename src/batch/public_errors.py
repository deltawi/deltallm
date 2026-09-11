from __future__ import annotations

from enum import StrEnum
from typing import Literal, NotRequired, TypedDict

from src.models.errors import RoutingFailureAction, ServiceUnavailableError
from src.router.execution import get_failover_original_error


class BatchPublicErrorCode(StrEnum):
    SELECTOR_CHECKPOINT_UNAVAILABLE = "batch_selector_checkpoint_unavailable"

    @property
    def message(self) -> str:
        return "Batch selector checkpoint is unavailable; selection cannot be repeated safely"


class BatchItemErrorBody(TypedDict):
    message: str
    type: Literal["BatchItemError", "BatchItemCancelled"]
    code: NotRequired[BatchPublicErrorCode]
    retryable: NotRequired[bool]
    retry_category: NotRequired[str]
    terminal_reason: NotRequired[str]
    attempt: NotRequired[int]
    max_attempts: NotRequired[int]
    retry_delay_seconds: NotRequired[int]


class BatchPublicError(ServiceUnavailableError):
    def __init__(self, code: BatchPublicErrorCode) -> None:
        super().__init__(
            message=code.message,
            code=code.value,
            affects_deployment_health=False,
            routing_failure_action=RoutingFailureAction.FAIL_FAST,
        )


def batch_public_error_code(value: object) -> BatchPublicErrorCode | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return BatchPublicErrorCode(value)
    except ValueError:
        return None


def exception_public_error_code(exc: Exception) -> BatchPublicErrorCode | None:
    original = get_failover_original_error(exc) or exc
    return (
        batch_public_error_code(original.code) if isinstance(original, BatchPublicError) else None
    )
