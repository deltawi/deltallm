"""Model pricing database.

Uses Decimal for precise financial calculations to avoid floating-point
precision errors with very small per-token costs (e.g., $0.00000015/token).
"""

from decimal import Decimal
from typing import Optional, Union
from dataclasses import dataclass


# Type alias for pricing dict values (Decimal for costs, int for limits)
PricingValue = Union[Decimal, int]


@dataclass(frozen=True)
class PricingInfo:
    """Pricing information for a model.

    All cost values use Decimal for precision. This is critical because
    per-token costs can be extremely small (e.g., $0.000000059 for Groq models)
    and floating-point arithmetic would introduce errors.
    """

    input_cost_per_token: Decimal = Decimal("0")
    output_cost_per_token: Decimal = Decimal("0")
    input_cost_per_image: Optional[Decimal] = None
    output_cost_per_image: Optional[Decimal] = None
    cache_creation_input_token_cost: Optional[Decimal] = None
    cache_read_input_token_cost: Optional[Decimal] = None


# Comprehensive model pricing database
# Prices use Decimal for exact representation of small values
# Note: Decimal("0.00015") / 1000 = exact Decimal("0.00000015")
MODEL_PRICES: dict[str, dict[str, PricingValue]] = {
    # OpenAI GPT-4o models
    "gpt-4o": {
        "input_cost_per_token": Decimal("0.0025") / 1000,
        "output_cost_per_token": Decimal("0.01") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 16384,
    },
    "gpt-4o-2024-11-20": {
        "input_cost_per_token": Decimal("0.0025") / 1000,
        "output_cost_per_token": Decimal("0.01") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 16384,
    },
    "gpt-4o-2024-08-06": {
        "input_cost_per_token": Decimal("0.0025") / 1000,
        "output_cost_per_token": Decimal("0.01") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 16384,
    },
    "gpt-4o-mini": {
        "input_cost_per_token": Decimal("0.00015") / 1000,
        "output_cost_per_token": Decimal("0.0006") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 16384,
    },
    "gpt-4o-mini-2024-07-18": {
        "input_cost_per_token": Decimal("0.00015") / 1000,
        "output_cost_per_token": Decimal("0.0006") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 16384,
    },
    # OpenAI GPT-4 Turbo models
    "gpt-4-turbo": {
        "input_cost_per_token": Decimal("0.01") / 1000,
        "output_cost_per_token": Decimal("0.03") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 4096,
    },
    "gpt-4-turbo-2024-04-09": {
        "input_cost_per_token": Decimal("0.01") / 1000,
        "output_cost_per_token": Decimal("0.03") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 4096,
    },
    "gpt-4-turbo-preview": {
        "input_cost_per_token": Decimal("0.01") / 1000,
        "output_cost_per_token": Decimal("0.03") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 4096,
    },
    # OpenAI GPT-4 models
    "gpt-4": {
        "input_cost_per_token": Decimal("0.03") / 1000,
        "output_cost_per_token": Decimal("0.06") / 1000,
        "max_tokens": 8192,
        "max_output_tokens": 8192,
    },
    "gpt-4-0613": {
        "input_cost_per_token": Decimal("0.03") / 1000,
        "output_cost_per_token": Decimal("0.06") / 1000,
        "max_tokens": 8192,
        "max_output_tokens": 8192,
    },
    "gpt-4-32k": {
        "input_cost_per_token": Decimal("0.06") / 1000,
        "output_cost_per_token": Decimal("0.12") / 1000,
        "max_tokens": 32768,
        "max_output_tokens": 32768,
    },
    "gpt-4-32k-0613": {
        "input_cost_per_token": Decimal("0.06") / 1000,
        "output_cost_per_token": Decimal("0.12") / 1000,
        "max_tokens": 32768,
        "max_output_tokens": 32768,
    },
    # OpenAI GPT-3.5 Turbo models
    "gpt-3.5-turbo": {
        "input_cost_per_token": Decimal("0.0005") / 1000,
        "output_cost_per_token": Decimal("0.0015") / 1000,
        "max_tokens": 16385,
        "max_output_tokens": 4096,
    },
    "gpt-3.5-turbo-0125": {
        "input_cost_per_token": Decimal("0.0005") / 1000,
        "output_cost_per_token": Decimal("0.0015") / 1000,
        "max_tokens": 16385,
        "max_output_tokens": 4096,
    },
    "gpt-3.5-turbo-1106": {
        "input_cost_per_token": Decimal("0.001") / 1000,
        "output_cost_per_token": Decimal("0.002") / 1000,
        "max_tokens": 16385,
        "max_output_tokens": 4096,
    },
    "gpt-3.5-turbo-16k": {
        "input_cost_per_token": Decimal("0.003") / 1000,
        "output_cost_per_token": Decimal("0.004") / 1000,
        "max_tokens": 16384,
        "max_output_tokens": 16384,
    },
    "gpt-3.5-turbo-instruct": {
        "input_cost_per_token": Decimal("0.0015") / 1000,
        "output_cost_per_token": Decimal("0.002") / 1000,
        "max_tokens": 4096,
        "max_output_tokens": 4096,
    },
    # OpenAI o1 models
    "o1": {
        "input_cost_per_token": Decimal("0.015") / 1000,
        "output_cost_per_token": Decimal("0.06") / 1000,
        "max_tokens": 200000,
        "max_output_tokens": 100000,
    },
    "o1-2024-12-17": {
        "input_cost_per_token": Decimal("0.015") / 1000,
        "output_cost_per_token": Decimal("0.06") / 1000,
        "max_tokens": 200000,
        "max_output_tokens": 100000,
    },
    "o1-preview": {
        "input_cost_per_token": Decimal("0.015") / 1000,
        "output_cost_per_token": Decimal("0.06") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 32768,
    },
    "o1-mini": {
        "input_cost_per_token": Decimal("0.003") / 1000,
        "output_cost_per_token": Decimal("0.012") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 65536,
    },
    # Anthropic Claude 3.5 models
    "claude-3-5-sonnet-20241022": {
        "input_cost_per_token": Decimal("0.003") / 1000,
        "output_cost_per_token": Decimal("0.015") / 1000,
        "cache_creation_input_token_cost": Decimal("0.00375") / 1000,
        "cache_read_input_token_cost": Decimal("0.0003") / 1000,
        "max_tokens": 200000,
        "max_output_tokens": 8192,
    },
    "claude-3-5-sonnet-20240620": {
        "input_cost_per_token": Decimal("0.003") / 1000,
        "output_cost_per_token": Decimal("0.015") / 1000,
        "max_tokens": 200000,
        "max_output_tokens": 8192,
    },
    "claude-3-5-haiku-20241022": {
        "input_cost_per_token": Decimal("0.0008") / 1000,
        "output_cost_per_token": Decimal("0.004") / 1000,
        "max_tokens": 200000,
        "max_output_tokens": 8192,
    },
    # Anthropic Claude 3 models
    "claude-3-opus-20240229": {
        "input_cost_per_token": Decimal("0.015") / 1000,
        "output_cost_per_token": Decimal("0.075") / 1000,
        "cache_creation_input_token_cost": Decimal("0.01875") / 1000,
        "cache_read_input_token_cost": Decimal("0.0015") / 1000,
        "max_tokens": 200000,
        "max_output_tokens": 4096,
    },
    "claude-3-sonnet-20240229": {
        "input_cost_per_token": Decimal("0.003") / 1000,
        "output_cost_per_token": Decimal("0.015") / 1000,
        "max_tokens": 200000,
        "max_output_tokens": 4096,
    },
    "claude-3-haiku-20240307": {
        "input_cost_per_token": Decimal("0.00025") / 1000,
        "output_cost_per_token": Decimal("0.00125") / 1000,
        "max_tokens": 200000,
        "max_output_tokens": 4096,
    },
    # Cohere models
    "command-r": {
        "input_cost_per_token": Decimal("0.0005") / 1000,
        "output_cost_per_token": Decimal("0.0015") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 4096,
    },
    "command-r-plus": {
        "input_cost_per_token": Decimal("0.003") / 1000,
        "output_cost_per_token": Decimal("0.015") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 4096,
    },
    # Mistral models
    "mistral-large-latest": {
        "input_cost_per_token": Decimal("0.002") / 1000,
        "output_cost_per_token": Decimal("0.006") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 8192,
    },
    "mistral-medium": {
        "input_cost_per_token": Decimal("0.0027") / 1000,
        "output_cost_per_token": Decimal("0.0081") / 1000,
        "max_tokens": 32000,
        "max_output_tokens": 8192,
    },
    "mistral-small": {
        "input_cost_per_token": Decimal("0.002") / 1000,
        "output_cost_per_token": Decimal("0.006") / 1000,
        "max_tokens": 32000,
        "max_output_tokens": 8192,
    },
    # Groq models
    "llama-3.1-70b-versatile": {
        "input_cost_per_token": Decimal("0.00059") / 1000,
        "output_cost_per_token": Decimal("0.00079") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 8192,
    },
    "llama-3.1-8b-instant": {
        "input_cost_per_token": Decimal("0.000059") / 1000,
        "output_cost_per_token": Decimal("0.000079") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 8192,
    },
    "mixtral-8x7b-32768": {
        "input_cost_per_token": Decimal("0.00024") / 1000,
        "output_cost_per_token": Decimal("0.00024") / 1000,
        "max_tokens": 32768,
        "max_output_tokens": 32768,
    },
    # Google Gemini models
    "gemini-1.5-pro": {
        "input_cost_per_token": Decimal("0.00125") / 1000,
        "output_cost_per_token": Decimal("0.005") / 1000,
        "max_tokens": 2000000,
        "max_output_tokens": 8192,
    },
    "gemini-1.5-flash": {
        "input_cost_per_token": Decimal("0.000075") / 1000,
        "output_cost_per_token": Decimal("0.0003") / 1000,
        "max_tokens": 1000000,
        "max_output_tokens": 8192,
    },
    "gemini-1.0-pro": {
        "input_cost_per_token": Decimal("0.0005") / 1000,
        "output_cost_per_token": Decimal("0.0015") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 2048,
    },
    # Azure OpenAI (same as OpenAI, but listed separately for clarity)
    "azure/gpt-4o": {
        "input_cost_per_token": Decimal("0.0025") / 1000,
        "output_cost_per_token": Decimal("0.01") / 1000,
        "max_tokens": 128000,
        "max_output_tokens": 16384,
    },
    "azure/gpt-4": {
        "input_cost_per_token": Decimal("0.03") / 1000,
        "output_cost_per_token": Decimal("0.06") / 1000,
        "max_tokens": 8192,
        "max_output_tokens": 8192,
    },
    # Embedding models
    "text-embedding-3-small": {
        "input_cost_per_token": Decimal("0.00002") / 1000,
        "max_tokens": 8191,
    },
    "text-embedding-3-large": {
        "input_cost_per_token": Decimal("0.00013") / 1000,
        "max_tokens": 8191,
    },
    "text-embedding-ada-002": {
        "input_cost_per_token": Decimal("0.0001") / 1000,
        "max_tokens": 8191,
    },
}


def _to_decimal(value: Optional[PricingValue]) -> Optional[Decimal]:
    """Convert a value to Decimal if not None."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def get_pricing_info(model: str) -> PricingInfo:
    """Get pricing information for a model.

    Args:
        model: The model name

    Returns:
        Pricing information with Decimal values for all costs
    """
    # Try exact match first
    if model in MODEL_PRICES:
        data = MODEL_PRICES[model]
        return PricingInfo(
            input_cost_per_token=_to_decimal(data.get("input_cost_per_token")) or Decimal("0"),
            output_cost_per_token=_to_decimal(data.get("output_cost_per_token")) or Decimal("0"),
            cache_creation_input_token_cost=_to_decimal(data.get("cache_creation_input_token_cost")),
            cache_read_input_token_cost=_to_decimal(data.get("cache_read_input_token_cost")),
        )

    # Try to find matching prefix
    for model_key, data in MODEL_PRICES.items():
        if model.startswith(model_key) or model_key in model:
            return PricingInfo(
                input_cost_per_token=_to_decimal(data.get("input_cost_per_token")) or Decimal("0"),
                output_cost_per_token=_to_decimal(data.get("output_cost_per_token")) or Decimal("0"),
                cache_creation_input_token_cost=_to_decimal(data.get("cache_creation_input_token_cost")),
                cache_read_input_token_cost=_to_decimal(data.get("cache_read_input_token_cost")),
            )

    # Return default pricing (free)
    return PricingInfo()


def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> Decimal:
    """Calculate the cost of a completion request.

    Uses Decimal arithmetic throughout to preserve precision for very small
    per-token costs. This is critical for accurate budget tracking.

    Args:
        model: The model name
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        cached_tokens: Number of cached tokens (for prompt caching)

    Returns:
        Total cost in USD as Decimal
    """
    pricing = get_pricing_info(model)

    # Calculate input cost using Decimal arithmetic
    uncached_tokens = prompt_tokens - cached_tokens
    input_cost = Decimal(uncached_tokens) * pricing.input_cost_per_token

    # Add cached token cost if applicable
    if cached_tokens > 0 and pricing.cache_read_input_token_cost:
        input_cost += Decimal(cached_tokens) * pricing.cache_read_input_token_cost
    elif cached_tokens > 0:
        input_cost += Decimal(cached_tokens) * pricing.input_cost_per_token

    # Calculate output cost
    output_cost = Decimal(completion_tokens) * pricing.output_cost_per_token

    return input_cost + output_cost


def get_model_info(model: str) -> dict[str, PricingValue]:
    """Get full model information including pricing and limits.

    Args:
        model: The model name

    Returns:
        Model information dictionary with Decimal costs and int limits
    """
    if model in MODEL_PRICES:
        return MODEL_PRICES[model]

    # Try prefix matching
    for model_key, data in MODEL_PRICES.items():
        if model.startswith(model_key) or model_key in model:
            return data

    return {
        "input_cost_per_token": Decimal("0"),
        "output_cost_per_token": Decimal("0"),
        "max_tokens": 4096,
        "max_output_tokens": 4096,
    }
