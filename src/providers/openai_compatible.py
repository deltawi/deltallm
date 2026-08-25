from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping

from src.models.errors import FailureClassification, InvalidRequestError, ProxyError
from src.providers.base import (
    ProviderErrorDetails,
    invalid_provider_response_error,
    is_valid_provider_token_count,
    map_standard_provider_status_error,
    provider_error_details_from_payload,
    validate_provider_success_payload,
)

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
    emitted_output = False
    saw_terminal = False

    async for line in provider_stream:
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if len(line) > _MAX_STREAM_FRAME_CHARS or not line.startswith("data:"):
            raise invalid_provider_response_error()

        raw_payload = line[len("data:") :].strip()
        if not raw_payload:
            continue
        if raw_payload == "[DONE]":
            if not emitted_output:
                raise invalid_provider_response_error()
            yield line
            return

        try:
            payload = json.loads(raw_payload)
        except (RecursionError, TypeError, ValueError) as exc:
            raise invalid_provider_response_error() from exc
        if not isinstance(payload, Mapping):
            raise invalid_provider_response_error()

        if "error" in payload:
            raise _map_openai_compatible_stream_error(payload, classify_failure)

        choices = payload.get("choices")
        if not isinstance(choices, list):
            raise invalid_provider_response_error()
        if not _valid_stream_choices(choices):
            raise invalid_provider_response_error()

        classified_stop = _content_filter_stop(choices)
        if classified_stop and not emitted_output:
            raise InvalidRequestError(
                message="Provider rejected request",
                affects_deployment_health=False,
                failure_classification=FailureClassification.CONTENT_POLICY,
            )

        has_output = _choices_have_output(choices)
        has_terminal = _choices_have_terminal(choices)
        if not emitted_output and not has_output and not has_terminal:
            pending_chars = _buffer_precommit_frame(pending, pending_chars, line)
            continue

        if not emitted_output:
            emitted_output = True
            for buffered in pending:
                yield buffered
            pending.clear()
        yield line
        saw_terminal = saw_terminal or has_terminal

    if not emitted_output or not saw_terminal:
        raise invalid_provider_response_error()


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


def _choices_have_output(choices: list[object]) -> bool:
    for value in choices:
        if not isinstance(value, Mapping):
            continue
        delta = value.get("delta")
        if not isinstance(delta, Mapping):
            continue
        for key in ("content", "refusal", "function_call", "tool_calls"):
            output = delta.get(key)
            if output not in (None, "", [], {}):
                return True
    return False


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


def _buffer_precommit_frame(pending: list[str], pending_chars: int, line: str) -> int:
    next_chars = pending_chars + len(line)
    if len(pending) >= _MAX_PRECOMMIT_STREAM_FRAMES or next_chars > _MAX_PRECOMMIT_STREAM_CHARS:
        raise invalid_provider_response_error()
    pending.append(line)
    return next_chars


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
    return invalid_provider_response_error()
