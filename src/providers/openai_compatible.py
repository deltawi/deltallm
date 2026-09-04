from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping

from src.metrics.counters import (
    ProviderStreamValidationFailureReason,
    increment_provider_stream_validation_failure,
)
from src.models.errors import (
    FailureClassification,
    InvalidRequestError,
    ProxyError,
    RoutingFailureAction,
)
from src.providers.base import (
    ProviderErrorDetails,
    invalid_provider_response_error,
    is_valid_provider_token_count,
    map_standard_provider_status_error,
    provider_error_details_from_payload,
    validate_provider_success_payload,
)
from src.providers.openai_stream_contract import inspect_openai_stream_choices

_MAX_PRECOMMIT_STREAM_FRAMES = 32
_MAX_PRECOMMIT_STREAM_CHARS = 262_144
_MAX_STREAM_FRAME_CHARS = 1_048_576
_RATE_LIMIT_IDENTIFIERS = frozenset(
    {
        "rate_limit",
        "rate_limit_error",
        "rate_limit_exceeded",
        "requests",
        "tokens",
    }
)
_CLIENT_ERROR_IDENTIFIERS = frozenset(
    {
        "authentication_error",
        "invalid_api_key",
        "invalid_request_error",
        "not_found_error",
        "permission_denied",
        "permission_error",
    }
)
_SERVER_ERROR_IDENTIFIERS = frozenset(
    {
        "api_error",
        "internal_error",
        "overloaded_error",
        "server_error",
    }
)

ProviderFailureClassifier = Callable[[ProviderErrorDetails], FailureClassification | None]


def validate_openai_compatible_chat_success(data: Mapping[str, object]) -> None:
    """Reject nominal chat successes that cannot represent a completion."""

    validate_provider_success_payload(data, _is_valid_openai_compatible_chat_success)


def _is_valid_openai_compatible_chat_success(data: Mapping[str, object]) -> bool:
    choices = data.get("choices")
    usage = data.get("usage")
    if not isinstance(choices, list) or not choices or not isinstance(usage, Mapping):
        return False

    indexes: set[int] = set()
    for value in choices:
        if not isinstance(value, Mapping):
            return False
        index = value.get("index")
        message = value.get("message")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index in indexes
            or not isinstance(message, Mapping)
        ):
            return False
        indexes.add(index)

    return all(
        is_valid_provider_token_count(usage.get(key))
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    )


async def translate_openai_compatible_stream(
    provider_stream: AsyncIterator[str],
    *,
    classify_failure: ProviderFailureClassifier,
) -> AsyncIterator[str]:
    """Validate an OpenAI-compatible stream before releasing its pre-output frames."""

    pending: list[str] = []
    pending_chars = 0
    pending_unknown_output_candidate = False
    emitted_output = False
    saw_terminal = False

    async for line in provider_stream:
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if len(line) > _MAX_STREAM_FRAME_CHARS:
            raise _invalid_stream_response_error(
                ProviderStreamValidationFailureReason.FRAME_TOO_LARGE
            )
        if not line.startswith("data:"):
            raise _invalid_stream_response_error(ProviderStreamValidationFailureReason.INVALID_SSE)

        raw_payload = line[len("data:") :].strip()
        if not raw_payload:
            continue
        if raw_payload == "[DONE]":
            if not emitted_output:
                reason = (
                    ProviderStreamValidationFailureReason.PRECOMMIT_UNKNOWN_OUTPUT_TERMINAL
                    if pending_unknown_output_candidate
                    else ProviderStreamValidationFailureReason.TERMINAL_BEFORE_OUTPUT
                )
                raise _invalid_stream_response_error(reason)
            yield line
            return

        try:
            payload = json.loads(raw_payload)
        except (RecursionError, TypeError, ValueError) as exc:
            raise _invalid_stream_response_error(
                ProviderStreamValidationFailureReason.INVALID_JSON
            ) from exc
        if not isinstance(payload, Mapping):
            raise _invalid_stream_response_error(
                ProviderStreamValidationFailureReason.INVALID_PAYLOAD
            )

        if "error" in payload:
            raise _map_openai_compatible_stream_error(payload, classify_failure)

        choices = payload.get("choices")
        if not isinstance(choices, list):
            raise _invalid_stream_response_error(
                ProviderStreamValidationFailureReason.INVALID_CHOICES
            )
        if not _valid_stream_choices(choices):
            raise _invalid_stream_response_error(
                ProviderStreamValidationFailureReason.INVALID_CHOICES
            )

        classified_stop = _content_filter_stop(choices)
        if classified_stop and not emitted_output:
            raise InvalidRequestError(
                message="Provider rejected request",
                affects_deployment_health=False,
                failure_classification=FailureClassification.CONTENT_POLICY,
            )

        inspection = inspect_openai_stream_choices(choices)
        has_output = inspection.has_output
        has_terminal = _choices_have_terminal(choices)
        if not emitted_output and not has_output and not has_terminal:
            pending_unknown_output_candidate = (
                pending_unknown_output_candidate or inspection.has_unknown_output_candidate
            )
            pending_chars = _buffer_precommit_frame(
                pending,
                pending_chars,
                line,
                has_unknown_output_candidate=pending_unknown_output_candidate,
            )
            continue

        if not emitted_output:
            emitted_output = True
            for buffered in pending:
                yield buffered
            pending.clear()
        yield line
        saw_terminal = saw_terminal or has_terminal

    if not emitted_output or not saw_terminal:
        raise _invalid_stream_response_error(
            ProviderStreamValidationFailureReason.INCOMPLETE_STREAM
        )


def _valid_stream_choices(choices: list[object]) -> bool:
    indexes: set[int] = set()
    for value in choices:
        if not isinstance(value, Mapping):
            return False
        index = value.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index in indexes:
            return False
        indexes.add(index)
        delta = value.get("delta")
        if delta is not None and not isinstance(delta, Mapping):
            return False
        finish_reason = value.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            return False
    return True


def _choices_have_terminal(choices: list[object]) -> bool:
    return any(
        isinstance(value, Mapping)
        and isinstance(value.get("finish_reason"), str)
        and bool(str(value["finish_reason"]).strip())
        for value in choices
    )


def _content_filter_stop(choices: list[object]) -> bool:
    return any(
        isinstance(value, Mapping)
        and isinstance(value.get("finish_reason"), str)
        and str(value["finish_reason"]).strip().lower() == "content_filter"
        for value in choices
    )


def _buffer_precommit_frame(
    pending: list[str],
    pending_chars: int,
    line: str,
    *,
    has_unknown_output_candidate: bool,
) -> int:
    next_chars = pending_chars + len(line)
    if len(pending) >= _MAX_PRECOMMIT_STREAM_FRAMES or next_chars > _MAX_PRECOMMIT_STREAM_CHARS:
        reason = (
            ProviderStreamValidationFailureReason.PRECOMMIT_UNKNOWN_OUTPUT_LIMIT
            if has_unknown_output_candidate
            else ProviderStreamValidationFailureReason.PRECOMMIT_NO_OUTPUT_LIMIT
        )
        raise _invalid_stream_response_error(reason)
    pending.append(line)
    return next_chars


def _invalid_stream_response_error(
    reason: ProviderStreamValidationFailureReason,
) -> ProxyError:
    increment_provider_stream_validation_failure(reason=reason)
    compatibility_failure = reason in {
        ProviderStreamValidationFailureReason.PRECOMMIT_UNKNOWN_OUTPUT_LIMIT,
        ProviderStreamValidationFailureReason.PRECOMMIT_UNKNOWN_OUTPUT_TERMINAL,
    }
    return invalid_provider_response_error(
        affects_deployment_health=not compatibility_failure,
        routing_failure_action=(
            RoutingFailureAction.NEXT_DEPLOYMENT if compatibility_failure else None
        ),
    )


def _map_openai_compatible_stream_error(
    payload: Mapping[str, object],
    classify_failure: ProviderFailureClassifier,
) -> ProxyError:
    details = provider_error_details_from_payload(payload, status_code=None)
    classification = classify_failure(details)
    if classification in {
        FailureClassification.CONTEXT_WINDOW,
        FailureClassification.CONTENT_POLICY,
    }:
        return map_standard_provider_status_error(400, failure_classification=classification)
    if details.identifiers & _RATE_LIMIT_IDENTIFIERS:
        return map_standard_provider_status_error(429)
    if details.identifiers & _CLIENT_ERROR_IDENTIFIERS:
        return map_standard_provider_status_error(400)
    if details.identifiers & _SERVER_ERROR_IDENTIFIERS:
        return map_standard_provider_status_error(500)
    return _invalid_stream_response_error(
        ProviderStreamValidationFailureReason.UNKNOWN_ERROR_ENVELOPE
    )
