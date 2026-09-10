from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field, JsonValue

from src.models.responses import ChatCompletionResponse, Usage
from src.models.errors import ProxyError
from src.providers.base import invalid_provider_response_error, is_valid_provider_token_count
from src.providers.token_receipt import ProviderTokenReceipt, token_receipt


class CompatibleChatUsage(Usage):
    prompt_tokens_cached: int = Field(default=0, ge=0)


class CompatibleChatResponse(ChatCompletionResponse):
    usage: CompatibleChatUsage


def normalize_chat_usage(value: object) -> dict[str, JsonValue]:
    """Map documented cache counters to the existing ledger vocabulary once."""
    receipt = _parse_chat_usage(value)
    return {
        "prompt_tokens": receipt.input_tokens,
        "completion_tokens": receipt.output_tokens,
        "total_tokens": receipt.total_tokens,
        "prompt_tokens_cached": receipt.cached_input_tokens or 0,
    }


def reported_chat_token_receipt(payload: object) -> ProviderTokenReceipt | None:
    """Preserve unknown usage for durable selector billing, including cache aliases."""
    if not isinstance(payload, Mapping):
        return None
    try:
        receipt = _parse_chat_usage(payload.get("usage"))
    except ProxyError:
        return None
    return token_receipt(
        input_tokens=receipt.input_tokens,
        output_tokens=receipt.output_tokens,
        total_tokens=receipt.total_tokens,
        cached_input_tokens=receipt.cached_input_tokens,
    )


def _parse_chat_usage(value: object) -> ProviderTokenReceipt:
    if not isinstance(value, Mapping):
        raise invalid_provider_response_error()
    totals = [value.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
    if not all(is_valid_provider_token_count(count) for count in totals):
        raise invalid_provider_response_error()
    prompt, completion, total = (int(count) for count in totals)
    if total != prompt + completion:
        raise invalid_provider_response_error()

    candidates = [value.get("prompt_tokens_cached"), value.get("prompt_cache_hit_tokens")]
    details = value.get("prompt_tokens_details")
    if details is not None:
        if not isinstance(details, Mapping):
            raise invalid_provider_response_error()
        candidates.append(details.get("cached_tokens"))
    present = [count for count in candidates if count is not None]
    if any(not is_valid_provider_token_count(count) or int(count) > prompt for count in present):
        raise invalid_provider_response_error()
    if len({int(count) for count in present}) > 1:
        raise invalid_provider_response_error()
    cached = int(present[0]) if present else None
    return ProviderTokenReceipt(prompt, completion, total, cached)
