from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping

import httpx

from src.models.errors import InvalidRequestError
from src.providers.json_guidance import validate_guided_json_support
from src.models.requests import ChatCompletionRequest, FunctionToolDefinition, ToolChoice
from src.providers.base import (
    invalid_provider_response_error,
    map_standard_provider_status_error,
    reject_openai_compatible_failure_response,
)
from src.providers.chat_profiles import ChatProviderProfile
from src.providers.chat_usage import (
    CompatibleChatResponse,
    normalize_chat_usage,
    reported_chat_token_receipt,
)
from src.providers.token_receipt import ProviderTokenReceipt
from src.providers.openai import OpenAIAdapter
from src.providers.openai_compatible import (
    translate_openai_compatible_stream,
    validate_openai_compatible_chat_success,
)
from src.providers.resolution import resolve_upstream_model

_CHAT_PARAMETERS = frozenset(
    {
        "model",
        "messages",
        "stream",
        "stream_options",
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "tools",
        "tool_choice",
        "response_format",
    }
)


def _normalize_mistral_tool_arguments(message: object) -> object:
    if not isinstance(message, Mapping):
        return message
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return message
    normalized: list[object] | None = None
    for index, call in enumerate(calls):
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        if not isinstance(function, Mapping) or not isinstance(function.get("arguments"), dict):
            continue
        # Mistral accepts object arguments. OpenAI clients require JSON strings,
        # while string deltas must retain their exact fragment boundaries.
        if normalized is None:
            normalized = list(calls)
        normalized[index] = {
            **call,
            "function": {
                **function,
                "arguments": json.dumps(function["arguments"], separators=(",", ":")),
            },
        }
    return {**message, "tool_calls": normalized} if normalized is not None else message


class ProfiledChatAdapter(OpenAIAdapter):
    """Compatible chat providers share validation, transport, and stream framing."""

    def __init__(self, http_client: httpx.AsyncClient, profile: ChatProviderProfile) -> None:
        super().__init__(http_client)
        self.profile = profile
        self.provider_name = profile.provider

    def reported_token_receipt(self, payload: object) -> ProviderTokenReceipt | None:
        return reported_chat_token_receipt(payload)

    async def translate_request(
        self, canonical_request: ChatCompletionRequest, provider_config: Mapping[str, object]
    ) -> dict[str, object]:
        validate_guided_json_support(canonical_request, provider=self.provider_name)
        # Only client-specified parameters are sent: OpenAI defaults are not
        # necessarily valid defaults for another vendor's model.
        payload = canonical_request.model_dump(mode="json", exclude_unset=True)
        # Protocol discriminators are required even when supplied by typed defaults.
        # Keep omission semantics everywhere else, including function JSON schemas.
        if canonical_request.tools:
            payload["tools"] = [
                {**tool.model_dump(mode="json", exclude_unset=True), "type": tool.type}
                if isinstance(tool, FunctionToolDefinition)
                else tool.model_dump(mode="json", exclude_unset=True)
                for tool in canonical_request.tools
            ]
        if isinstance(canonical_request.tool_choice, ToolChoice):
            payload["tool_choice"] = {
                **canonical_request.tool_choice.model_dump(mode="json", exclude_unset=True),
                "type": canonical_request.tool_choice.type,
            }
        payload.pop("metadata", None)
        for parameter, neutral in (("n", 1), ("presence_penalty", 0), ("frequency_penalty", 0)):
            if parameter in payload and payload[parameter] not in (None, neutral):
                raise InvalidRequestError(message=f"Parameter '{parameter}' is not supported")
            payload.pop(parameter, None)
        payload = {key: value for key, value in payload.items() if value is not None}
        unsupported = payload.keys() - _CHAT_PARAMETERS
        if unsupported:
            raise InvalidRequestError(
                message=f"Parameter '{sorted(unsupported)[0]}' is not supported"
            )
        if not payload.get("tools"):
            payload.pop("tool_choice", None)
        if self.profile.provider == "minimax":
            # Keep reasoning out of visible answer content and preserve the
            # returned blocks when the client continues a tool conversation.
            payload["reasoning_split"] = True
        if not self.profile.stream_usage:
            payload.pop("stream_options", None)
        payload["model"] = resolve_upstream_model(provider_config)
        return payload

    async def translate_response(
        self, provider_response: object, model_name: str
    ) -> CompatibleChatResponse:
        if not isinstance(provider_response, Mapping):
            raise invalid_provider_response_error()
        reject_openai_compatible_failure_response(provider_response)
        validate_openai_compatible_chat_success(provider_response)
        usage = normalize_chat_usage(provider_response.get("usage"))
        choices = provider_response.get("choices")
        if isinstance(choices, list):
            choices = self._normalize_choices(choices)
        # Reuse validation without the legacy adapter's null-to-empty-string
        # conversion or mutation of nested provider response objects.
        return CompatibleChatResponse.model_validate(
            {
                **provider_response,
                "choices": choices,
                "model": provider_response.get("model", model_name),
                "usage": usage,
            }
        )

    def _normalize_choices(self, choices: list[object]) -> list[object]:
        if self.profile.provider != "mistral":
            return choices
        normalized: list[object] | None = None
        for index, choice in enumerate(choices):
            if not isinstance(choice, Mapping):
                continue
            reason = choice.get("finish_reason")
            if reason == "error":
                # A native failure must not commit a pre-output stream or emit
                # a successful terminal frame after partial output.
                raise map_standard_provider_status_error(503)
            updated = {**choice, "finish_reason": "length"} if reason == "model_length" else choice
            for field in ("message", "delta"):
                message = choice.get(field)
                normalized_message = _normalize_mistral_tool_arguments(message)
                if normalized_message is not message:
                    updated = {**updated, field: normalized_message}
            if updated is not choice:
                if normalized is None:
                    normalized = list(choices)
                normalized[index] = updated
        return normalized if normalized is not None else choices

    async def translate_stream(
        self,
        provider_stream: AsyncIterator[str],
        *,
        model_name: str | None = None,
        output_observer: Callable[[int | None], None] | None = None,
    ) -> AsyncIterator[str]:
        async for line in translate_openai_compatible_stream(
            provider_stream,
            classify_failure=self._classify_failure,
            normalize_usage=normalize_chat_usage,
            normalize_choices=self._normalize_choices
            if self.profile.provider == "mistral"
            else None,
            output_observer=output_observer,
            output_count=self.complete_output_count,
        ):
            yield line
