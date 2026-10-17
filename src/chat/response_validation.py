from __future__ import annotations

import json
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, TypeVar

from src.chat.json_healing import heal_json_text
from src.metrics import (
    increment_response_json_healed,
    increment_response_validation_exhausted,
    increment_response_validation_failure,
)
from src.models.errors import ProxyError, RoutingFailureAction, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest, FunctionToolDefinition
from src.providers.base import INVALID_PROVIDER_RESPONSE_MESSAGE
from src.route_group_config import ResponseValidationSettings
from src.router.execution import ProviderAttemptSuccess
from src.router.router import Deployment

RESPONSE_VALIDATION_HEADER: Final = "x-deltallm-response-validation"
RESPONSE_HEALED_HEADER: Final = "x-deltallm-response-healed"
RESPONSE_VALIDATION_ERROR_CODE: Final = "invalid_provider_response"
_MAX_VALIDATED_JSON_CHARS: Final = 1_048_576

ChatAttemptResult = tuple[dict[str, Any], float]
_AttemptT = TypeVar("_AttemptT", ChatAttemptResult, ProviderAttemptSuccess[ChatAttemptResult])


class ResponseValidationReason(StrEnum):
    EMPTY_RESPONSE = "empty_response"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    UNKNOWN_TOOL_NAME = "unknown_tool_name"
    INVALID_JSON_OUTPUT = "invalid_json_output"


class ResponseValidationExhaustedOutcome(StrEnum):
    RETURNED_RESPONSE = "returned_response"
    RETURNED_ERROR = "returned_error"


class ResponseValidationError(ServiceUnavailableError):
    """A nominal provider success that the gateway rejects so another attempt can answer."""

    def __init__(self, reason: ResponseValidationReason) -> None:
        super().__init__(
            message=INVALID_PROVIDER_RESPONSE_MESSAGE,
            code=RESPONSE_VALIDATION_ERROR_CODE,
            # Quality defects steer retries and fallbacks; they never cool down a deployment.
            affects_deployment_health=False,
            routing_failure_action=RoutingFailureAction.RETRY_OR_NEXT,
        )
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _RejectedResponse:
    result: ChatAttemptResult
    deployment: Deployment
    reason: ResponseValidationReason


def find_response_defect(
    response: Mapping[str, Any],
    request: ChatCompletionRequest,
    settings: ResponseValidationSettings,
) -> ResponseValidationReason | None:
    """Return the first enabled defect in a canonical chat completion, or None."""

    if not settings.enabled:
        return None
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    messages = [_choice_message(choice) for choice in choices]
    if settings.empty_response and _all_choices_empty(choices, messages):
        return ResponseValidationReason.EMPTY_RESPONSE
    tool_calls = [call for message in messages for call in _tool_calls(message)]
    if settings.invalid_tool_arguments and any(
        not _valid_tool_arguments(call) for call in tool_calls
    ):
        return ResponseValidationReason.INVALID_TOOL_ARGUMENTS
    if settings.unknown_tool_name and tool_calls:
        offered = _offered_function_names(request)
        if offered is not None and any(_tool_name(call) not in offered for call in tool_calls):
            return ResponseValidationReason.UNKNOWN_TOOL_NAME
    if settings.invalid_json_output and _json_output_requested(request):
        if any(
            not _valid_json_content(choice, message)
            for choice, message in zip(choices, messages, strict=True)
        ):
            return ResponseValidationReason.INVALID_JSON_OUTPUT
    return None


class ResponseValidationScope:
    """Own validation for one failover call and the response kept when attempts run out."""

    __slots__ = ("_request", "_settings", "_rejected", "served_defect", "served_healed")

    def __init__(
        self, request: ChatCompletionRequest, settings: ResponseValidationSettings
    ) -> None:
        self._request = request
        self._settings = settings
        self._rejected: _RejectedResponse | None = None
        self.served_defect: ResponseValidationReason | None = None
        self.served_healed = False

    async def checked(self, deployment: Deployment, attempt: Awaitable[_AttemptT]) -> _AttemptT:
        outcome = await attempt
        result: ChatAttemptResult = (
            outcome.value if isinstance(outcome, ProviderAttemptSuccess) else outcome
        )
        healed = (
            self._settings.enabled
            and self._settings.json_healing
            and heal_json_choices(result[0], self._request)
        )
        defect = find_response_defect(result[0], self._request, self._settings)
        if defect is None:
            if healed:
                increment_response_json_healed(deployment_id=deployment.deployment_id)
            self.served_healed = healed
            return outcome
        increment_response_validation_failure(
            deployment_id=deployment.deployment_id, reason=defect.value
        )
        self._rejected = _RejectedResponse(result=result, deployment=deployment, reason=defect)
        raise ResponseValidationError(defect)

    async def run(
        self, failover: Awaitable[tuple[ChatAttemptResult, Deployment]]
    ) -> tuple[ChatAttemptResult, Deployment]:
        """Serve the last rejected answer when no other attempt produced one.

        Without this, a model that every deployment answers imperfectly would turn a
        usable answer into a 503 that the client never got before validation existed.
        """

        try:
            return await failover
        except ProxyError:
            rejected = self._rejected
            if rejected is None:
                raise
            if self._settings.on_exhausted == "error":
                increment_response_validation_exhausted(
                    reason=rejected.reason.value,
                    outcome=ResponseValidationExhaustedOutcome.RETURNED_ERROR.value,
                )
                raise
            increment_response_validation_exhausted(
                reason=rejected.reason.value,
                outcome=ResponseValidationExhaustedOutcome.RETURNED_RESPONSE.value,
            )
            self.served_defect = rejected.reason
            return rejected.result, rejected.deployment

    def response_headers(self) -> dict[str, str]:
        if self.served_defect is not None:
            return {RESPONSE_VALIDATION_HEADER: self.served_defect.value}
        if self.served_healed:
            return {RESPONSE_HEALED_HEADER: "json"}
        return {}


def heal_json_choices(response: dict[str, Any], request: ChatCompletionRequest) -> bool:
    """Repair JSON-mode answers in place; return whether any content changed."""

    if not _json_output_requested(request):
        return False
    require_object = request.response_format is not None and (
        request.response_format.type == "json_object"
    )
    choices = response.get("choices")
    if not isinstance(choices, list):
        return False
    changed = False
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            continue
        if _valid_json_content(choice, message):
            continue
        healed = heal_json_text(message["content"], require_object=require_object)
        if healed is not None:
            message["content"] = healed
            changed = True
    return changed


def _choice_message(choice: object) -> Mapping[str, Any]:
    if isinstance(choice, Mapping):
        message = choice.get("message")
        if isinstance(message, Mapping):
            return message
    return {}


def _finish_reason(choice: object) -> str | None:
    if isinstance(choice, Mapping) and isinstance(choice.get("finish_reason"), str):
        return str(choice["finish_reason"])
    return None


def _all_choices_empty(choices: Sequence[object], messages: Sequence[Mapping[str, Any]]) -> bool:
    for choice, message in zip(choices, messages, strict=True):
        # A length stop is the caller's token budget, not a provider defect.
        if _finish_reason(choice) == "length":
            return False
        if _tool_calls(message) or _has_text(message.get("content")):
            return False
        if _has_text(message.get("reasoning_content")) or message.get("reasoning_details"):
            return False
        # A refusal or an audio answer is output, even without text content.
        if _has_text(message.get("refusal")) or message.get("audio"):
            return False
    return True


def _has_text(content: object) -> bool:
    if isinstance(content, str):
        return bool(content) and not content.isspace()
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") != "text" or _has_text(part.get("text")):
                return True
    return False


def _tool_calls(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return []
    return [call for call in calls if isinstance(call, Mapping)]


def _function(call: Mapping[str, Any]) -> Mapping[str, Any]:
    function = call.get("function")
    return function if isinstance(function, Mapping) else {}


def _tool_name(call: Mapping[str, Any]) -> str | None:
    name = _function(call).get("name")
    return name if isinstance(name, str) else None


def _valid_tool_arguments(call: Mapping[str, Any]) -> bool:
    if call.get("type", "function") != "function":
        return True
    arguments = _function(call).get("arguments")
    if arguments is None:
        return True
    if not isinstance(arguments, str):
        return isinstance(arguments, Mapping)
    # Some providers send "" for a tool without parameters; clients treat it as {}.
    if not arguments.strip():
        return True
    parsed = _parse_json(arguments)
    return parsed is _UNCHECKED or isinstance(parsed, dict)


def _offered_function_names(request: ChatCompletionRequest) -> frozenset[str] | None:
    """Return offered names, or None when the request has tools the gateway cannot name."""

    tools = request.tools
    if not tools:
        return frozenset()
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, FunctionToolDefinition):
            return None
        name = tool.function.get("name")
        if not isinstance(name, str) or not name:
            return None
        names.add(name)
    return frozenset(names)


def _json_output_requested(request: ChatCompletionRequest) -> bool:
    response_format = request.response_format
    return response_format is not None and response_format.type in {"json_object", "json_schema"}


def _valid_json_content(choice: object, message: Mapping[str, Any]) -> bool:
    if _finish_reason(choice) == "length" or _tool_calls(message):
        return True
    content = message.get("content")
    if not isinstance(content, str):
        return content is None
    return _parse_json(content) is not _INVALID_JSON


_INVALID_JSON: Final = object()
_UNCHECKED: Final = object()


def _parse_json(text: str) -> object:
    # Oversized text is left as is; validation must not cost more than the response.
    if len(text) > _MAX_VALIDATED_JSON_CHARS:
        return _UNCHECKED
    try:
        return json.loads(text)
    except (RecursionError, ValueError):
        return _INVALID_JSON
