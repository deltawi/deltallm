from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, AsyncIterator

import httpx

from src.models.errors import FailureClassification, InvalidRequestError, ProxyError
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.base import (
    ProviderAdapter,
    ProviderErrorDetails,
    classify_provider_failure,
    invalid_provider_response_error,
    is_valid_provider_token_count,
    map_standard_provider_error,
    map_standard_provider_status_error,
    provider_error_details,
    validate_provider_success_payload,
)
from src.providers.healthcheck import is_provider_healthy

_CONTEXT_IDENTIFIERS = frozenset(
    {
        "context_length_exceeded",
        "context_window_exceeded",
        "input_too_long",
    }
)
_CONTENT_IDENTIFIERS = frozenset(
    {
        "blocklist",
        "content_blocked",
        "escalation",
        "image_prohibited_content",
        "image_recitation",
        "image_safety",
        "prohibited_content",
        "recitation",
        "safety",
        "spii",
    }
)
_CONTEXT_MESSAGE_MARKERS = ("input context is too long", "prompt is too long")
_CONTENT_MESSAGE_MARKERS = ("blocked due to safety", "prohibited content")
_SUCCESS_FINISH_REASONS = frozenset({"max_tokens", "stop"})


def _classify_gemini_failure(
    details: ProviderErrorDetails,
) -> FailureClassification | None:
    return classify_provider_failure(
        details,
        context_identifiers=_CONTEXT_IDENTIFIERS,
        content_identifiers=_CONTENT_IDENTIFIERS,
        context_message_markers=_CONTEXT_MESSAGE_MARKERS,
        content_message_markers=_CONTENT_MESSAGE_MARKERS,
    )


def _gemini_response_failure(data: dict[str, Any]) -> ProxyError | None:
    identifiers: set[str] = set()
    block_reason: str | None = None
    prompt_feedback = data.get("promptFeedback")
    if isinstance(prompt_feedback, dict):
        raw_block_reason = prompt_feedback.get("blockReason")
        if isinstance(raw_block_reason, str) and raw_block_reason.strip():
            block_reason = raw_block_reason.strip().lower()
            identifiers.add(block_reason)

    finish_reason: str | None = None
    candidates = data.get("candidates")
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        raw_finish_reason = candidates[0].get("finishReason")
        if isinstance(raw_finish_reason, str) and raw_finish_reason.strip():
            finish_reason = raw_finish_reason.strip().lower()
            identifiers.add(finish_reason)

    classification = _classify_gemini_failure(
        ProviderErrorDetails(status_code=400, identifiers=frozenset(identifiers))
    )
    if classification is not None:
        return map_standard_provider_status_error(
            400,
            failure_classification=classification,
        )
    if block_reason is not None or (
        finish_reason is not None and finish_reason not in _SUCCESS_FINISH_REASONS
    ):
        return invalid_provider_response_error()
    return None


def reject_gemini_failure_response(data: dict[str, Any]) -> None:
    """Reject documented nominal-success envelopes that represent failure."""

    if failure := _gemini_response_failure(data):
        raise failure


def _is_valid_gemini_success_payload(data: Mapping[str, Any]) -> bool:
    candidates = data.get("candidates")
    usage = data.get("usageMetadata")
    if (
        not isinstance(candidates, list)
        or not candidates
        or not isinstance(candidates[0], Mapping)
        or not isinstance(usage, Mapping)
    ):
        return False
    first = candidates[0]
    content = first.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None
    return (
        isinstance(first.get("finishReason"), str)
        and bool(str(first["finishReason"]).strip())
        and isinstance(parts, list)
        and all(isinstance(part, Mapping) for part in parts)
        and is_valid_provider_token_count(usage.get("promptTokenCount"))
        and is_valid_provider_token_count(usage.get("candidatesTokenCount"))
        and is_valid_provider_token_count(usage.get("totalTokenCount"))
    )


class GeminiAdapter(ProviderAdapter):
    provider_name = "gemini"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def translate_request(
        self,
        canonical_request: ChatCompletionRequest,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        if canonical_request.tools or any(
            message.role == "tool" or bool(message.tool_calls)
            for message in canonical_request.messages
        ):
            raise InvalidRequestError(
                message="Provider 'gemini' does not support tool calling",
                param="tools",
            )

        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []
        for message in canonical_request.messages:
            role = message.role
            content = message.content
            if isinstance(content, list):
                text = "\n".join(
                    str(part.get("text", "")) if isinstance(part, dict) else str(part)
                    for part in content
                )
            else:
                text = str(content or "")
            if role == "system":
                if text:
                    system_parts.append({"text": text})
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})

        payload: dict[str, Any] = {
            "contents": contents or [{"role": "user", "parts": [{"text": ""}]}]
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        generation_config: dict[str, Any] = {}
        if canonical_request.temperature is not None:
            generation_config["temperature"] = canonical_request.temperature
        if canonical_request.top_p is not None:
            generation_config["topP"] = canonical_request.top_p
        if canonical_request.max_tokens is not None:
            generation_config["maxOutputTokens"] = canonical_request.max_tokens
        if canonical_request.stop:
            generation_config["stopSequences"] = (
                canonical_request.stop
                if isinstance(canonical_request.stop, list)
                else [canonical_request.stop]
            )
        if generation_config:
            payload["generationConfig"] = generation_config

        return payload

    async def translate_response(
        self, provider_response: Any, model_name: str
    ) -> ChatCompletionResponse:
        data = (
            provider_response
            if isinstance(provider_response, dict)
            else json.loads(provider_response)
        )
        reject_gemini_failure_response(data)
        validate_provider_success_payload(data, _is_valid_gemini_success_payload)
        candidates = data.get("candidates") or []
        first = candidates[0] if candidates else {}
        content = first.get("content") or {}
        parts = content.get("parts") or []
        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
        finish_reason = (
            "length" if first["finishReason"].strip().lower() == "max_tokens" else "stop"
        )

        usage = data.get("usageMetadata") or {}
        prompt_tokens = int(usage.get("promptTokenCount") or 0)
        completion_tokens = int(usage.get("candidatesTokenCount") or 0)
        total_tokens = int(usage.get("totalTokenCount") or (prompt_tokens + completion_tokens))

        canonical = {
            "id": data.get("responseId") or f"chatcmpl-gemini-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }
        return ChatCompletionResponse.model_validate(canonical)

    async def translate_stream(
        self,
        provider_stream: AsyncIterator[str],
        *,
        model_name: str | None = None,
    ) -> AsyncIterator[str]:
        # Native Gemini stream translation is not implemented in this phase.
        if False:
            yield ""
        raise InvalidRequestError(message="Gemini streaming is not supported yet")

    def map_error(
        self,
        provider_error: Exception,
        *,
        details: ProviderErrorDetails | None = None,
    ) -> ProxyError:
        classification = _classify_gemini_failure(details or provider_error_details(provider_error))
        return map_standard_provider_error(
            provider_error,
            failure_classification=classification,
        )

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        return await is_provider_healthy(
            self.http_client,
            provider_config,
            default_openai_base_url="https://api.openai.com/v1",
            default_provider=self.provider_name,
        )
