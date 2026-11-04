"""Pricing and limit hints read from provider model-list responses.

Provider list APIs often return a model's context window, output limit and
(for some providers) prices next to its id. These values only pre-fill the
admin model form; the admin still reviews and saves them. Every value is
checked for type and range so a malformed or hostile response cannot inject
nonsense into the form.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

_MAX_TOKEN_LIMIT = 100_000_000
# 0.01 USD per token is 10,000 USD per million tokens, far above any real price.
_MAX_TOKEN_PRICE = 0.01
_PER_MILLION = 1_000_000

LIMIT_KEYS = frozenset({"max_tokens", "max_input_tokens", "max_output_tokens"})
PRICE_KEYS = frozenset(
    {"input_cost_per_token", "output_cost_per_token", "input_cost_per_token_cache_hit"}
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _limit(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer() or not 1 <= number <= _MAX_TOKEN_LIMIT:
        return None
    return int(number)


def _price(value: Any, *, scale: float = 1.0) -> float | None:
    number = _number(value)
    if number is None:
        return None
    number = number / scale
    # Negative prices are "variable pricing" sentinels (OpenRouter auto routes).
    if not 0 <= number <= _MAX_TOKEN_PRICE:
        return None
    return number


def _set_limit(metadata: dict[str, float | int], key: str, value: Any) -> None:
    limit = _limit(value)
    if limit is not None and key not in metadata:
        metadata[key] = limit


def _set_price(
    metadata: dict[str, float | int], key: str, value: Any, *, scale: float = 1.0
) -> None:
    price = _price(value, scale=scale)
    if price is not None and key not in metadata:
        metadata[key] = price


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def openai_style_metadata(item: Mapping[str, Any], *, provider: str) -> dict[str, float | int]:
    """Reads limits and prices from one entry of an OpenAI-style ``/models`` list.

    Field names by provider:
    - OpenRouter: ``context_length``, ``top_provider.max_completion_tokens``,
      ``pricing.prompt`` / ``completion`` / ``input_cache_read`` (USD per token).
    - Groq: ``context_window``, ``max_completion_tokens``.
    - Together: ``context_length``, ``pricing.input`` / ``output`` (USD per million tokens).
    - vLLM and SGLang: ``max_model_len``.
    - Fireworks and others: ``context_length`` when present.
    """
    metadata: dict[str, float | int] = {}
    for key in ("context_length", "context_window", "max_model_len"):
        _set_limit(metadata, "max_tokens", item.get(key))
    _set_limit(metadata, "max_output_tokens", item.get("max_completion_tokens"))
    _set_limit(
        metadata,
        "max_output_tokens",
        _mapping(item.get("top_provider")).get("max_completion_tokens"),
    )

    pricing = _mapping(item.get("pricing"))
    if provider == "openrouter":
        _set_price(metadata, "input_cost_per_token", pricing.get("prompt"))
        _set_price(metadata, "output_cost_per_token", pricing.get("completion"))
        _set_price(metadata, "input_cost_per_token_cache_hit", pricing.get("input_cache_read"))
    elif provider == "together":
        _set_price(metadata, "input_cost_per_token", pricing.get("input"), scale=_PER_MILLION)
        _set_price(metadata, "output_cost_per_token", pricing.get("output"), scale=_PER_MILLION)
    return metadata


def gemini_metadata(item: Mapping[str, Any]) -> dict[str, float | int]:
    """Gemini reports separate input and output limits, not one total window."""
    metadata: dict[str, float | int] = {}
    _set_limit(metadata, "max_input_tokens", item.get("inputTokenLimit"))
    _set_limit(metadata, "max_output_tokens", item.get("outputTokenLimit"))
    return metadata


def anthropic_metadata(item: Mapping[str, Any]) -> dict[str, float | int]:
    """Anthropic ``max_input_tokens`` is the input window; ``max_tokens`` caps output."""
    metadata: dict[str, float | int] = {}
    _set_limit(metadata, "max_input_tokens", item.get("max_input_tokens"))
    _set_limit(metadata, "max_output_tokens", item.get("max_tokens"))
    return metadata


def mistral_metadata(max_context_length: Any) -> dict[str, float | int]:
    metadata: dict[str, float | int] = {}
    _set_limit(metadata, "max_tokens", max_context_length)
    return metadata


def merge_known_metadata(
    curated: Mapping[str, Any] | None, discovered: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Curated catalog values win; provider values fill the keys it lacks."""
    merged = {**dict(discovered or {}), **dict(curated or {})}
    return merged or None
