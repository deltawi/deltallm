from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderTokenReceipt:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int | None = None


TokenReceiptObserver = Callable[[ProviderTokenReceipt | None], None]


def token_receipt(
    *,
    input_tokens: object,
    output_tokens: object,
    total_tokens: object,
    cached_input_tokens: object = None,
) -> ProviderTokenReceipt | None:
    values = (input_tokens, output_tokens, total_tokens)
    if any(type(value) is not int or not 0 <= value <= 2**31 - 1 for value in values):
        return None
    if total_tokens != input_tokens + output_tokens:
        return None
    if cached_input_tokens is not None and (
        type(cached_input_tokens) is not int or not 0 <= cached_input_tokens <= input_tokens
    ):
        return None
    return ProviderTokenReceipt(input_tokens, output_tokens, total_tokens, cached_input_tokens)


def openai_token_receipt(payload: object) -> ProviderTokenReceipt | None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("usage"), Mapping):
        return None
    usage = payload["usage"]
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
    return token_receipt(
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        cached_input_tokens=cached,
    )


def native_token_receipt(
    payload: object,
    *,
    usage_key: str,
    input_key: str,
    output_key: str,
    total_key: str,
    cache_key: str,
    unsupported_usage_keys: tuple[str, ...] = (),
) -> ProviderTokenReceipt | None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get(usage_key), Mapping):
        return None
    usage = payload[usage_key]
    if any(usage.get(key, 0) != 0 for key in unsupported_usage_keys):
        return None
    return token_receipt(
        input_tokens=usage.get(input_key),
        output_tokens=usage.get(output_key),
        total_tokens=usage.get(total_key),
        cached_input_tokens=usage.get(cache_key),
    )


def anthropic_token_receipt(payload: object) -> ProviderTokenReceipt | None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("usage"), Mapping):
        return None
    usage = payload["usage"]
    # Cache creation has a separate pricing dimension not supported by the frozen
    # token/request price contract. Preserve unknown instead of silently undercharging.
    if usage.get("cache_creation_input_tokens", 0) != 0:
        return None
    cached = usage.get("cache_read_input_tokens")
    uncached, output = usage.get("input_tokens"), usage.get("output_tokens")
    cache_count = 0 if cached is None else cached
    if any(type(value) is not int or value < 0 for value in (uncached, output, cache_count)):
        return None
    total_input = uncached + cache_count
    return token_receipt(
        input_tokens=total_input,
        output_tokens=output,
        total_tokens=total_input + output,
        cached_input_tokens=cached,
    )
