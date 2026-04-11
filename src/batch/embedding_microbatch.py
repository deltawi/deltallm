from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from src.models.requests import EmbeddingRequest

EmbeddingInputKind = Literal["string", "token_ids", "string_array", "token_id_array", "empty_array", "unknown"]


@dataclass(frozen=True, slots=True)
class _ExecutionSignature:
    model_name: str
    model_group: str
    primary_deployment_id: str
    encoding_format: str | None
    dimensions: int | None
    user: str | None
    input_kind: EmbeddingInputKind


def estimate_embedding_microbatch_weight(payload: EmbeddingRequest) -> int:
    from src.middleware.rate_limit import estimate_tokens

    return max(1, estimate_tokens(payload.input))


def resolve_effective_upstream_max_batch_inputs(model_info: Mapping[str, Any] | None) -> int:
    if model_info is None:
        return 1

    raw_value = model_info.get("upstream_max_batch_inputs")
    if raw_value is None:
        return 1

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


def classify_embedding_microbatch_request(payload: EmbeddingRequest) -> tuple[EmbeddingInputKind, bool, str | None]:
    input_value = payload.input

    if isinstance(input_value, str):
        return "string", True, None

    if not isinstance(input_value, list):
        return "unknown", False, "unsupported_input_shape"

    if not input_value:
        return "empty_array", False, "empty_input_array"

    if all(isinstance(item, int) for item in input_value):
        return "token_ids", True, None

    if all(isinstance(item, str) for item in input_value):
        return "string_array", False, "multi_input_text_array"

    if all(isinstance(item, list) and all(isinstance(token, int) for token in item) for item in input_value):
        return "token_id_array", False, "multi_input_token_array"

    return "unknown", False, "unsupported_input_shape"


def build_embedding_execution_signature(
    *,
    payload: EmbeddingRequest,
    model_group: str,
    primary_deployment_id: str,
    input_kind: EmbeddingInputKind,
) -> _ExecutionSignature:
    return _ExecutionSignature(
        model_name=payload.model,
        model_group=model_group,
        primary_deployment_id=primary_deployment_id,
        encoding_format=payload.encoding_format,
        dimensions=payload.dimensions,
        user=payload.user,
        input_kind=input_kind,
    )


def allocate_embedding_usage(
    aggregate_usage: Mapping[str, Any] | None,
    *,
    item_weights: Sequence[int],
) -> list[dict[str, int]]:
    count = len(item_weights)
    if count == 0:
        return []

    usage = dict(aggregate_usage or {})
    prompt_tokens_total = _coerce_non_negative_int(usage.get("prompt_tokens"))
    cached_prompt_tokens_total = _coerce_non_negative_int(usage.get("prompt_tokens_cached"))
    completion_tokens_total = _coerce_non_negative_int(usage.get("completion_tokens"))
    total_tokens_total = _coerce_non_negative_int(usage.get("total_tokens"))

    include_prompt_tokens = "prompt_tokens" in usage or prompt_tokens_total > 0
    include_cached_prompt_tokens = "prompt_tokens_cached" in usage or cached_prompt_tokens_total > 0

    prompt_tokens = _allocate_proportional_ints(prompt_tokens_total, item_weights) if include_prompt_tokens else [0] * count

    cached_prompt_weights = prompt_tokens if sum(prompt_tokens) > 0 else item_weights
    cached_prompt_tokens = (
        _allocate_proportional_ints(cached_prompt_tokens_total, cached_prompt_weights)
        if include_cached_prompt_tokens
        else [0] * count
    )

    include_completion_tokens = (
        "completion_tokens" in usage
        or completion_tokens_total > 0
        or include_prompt_tokens
        or "total_tokens" in usage
        or total_tokens_total > 0
    )
    completion_tokens = (
        _allocate_proportional_ints(completion_tokens_total, item_weights)
        if include_completion_tokens
        else [0] * count
    )

    include_total_tokens = (
        "total_tokens" in usage
        or total_tokens_total > 0
        or include_prompt_tokens
        or include_completion_tokens
    )
    if not include_total_tokens:
        total_tokens = [0] * count
    elif total_tokens_total == prompt_tokens_total + completion_tokens_total:
        total_tokens = [prompt + completion for prompt, completion in zip(prompt_tokens, completion_tokens, strict=False)]
    elif "total_tokens" in usage or total_tokens_total > 0:
        total_tokens = _allocate_proportional_ints(total_tokens_total, item_weights)
    else:
        total_tokens = [prompt + completion for prompt, completion in zip(prompt_tokens, completion_tokens, strict=False)]

    allocations: list[dict[str, int]] = []
    for index in range(count):
        allocation: dict[str, int] = {}
        if include_prompt_tokens:
            allocation["prompt_tokens"] = prompt_tokens[index]
        if include_cached_prompt_tokens:
            allocation["prompt_tokens_cached"] = cached_prompt_tokens[index]
        if include_completion_tokens:
            allocation["completion_tokens"] = completion_tokens[index]
        if include_total_tokens:
            allocation["total_tokens"] = total_tokens[index]
        allocations.append(allocation)
    return allocations


def _allocate_proportional_ints(total: int, weights: Sequence[int]) -> list[int]:
    count = len(weights)
    if count == 0:
        return []

    normalized_total = max(0, int(total))
    if normalized_total == 0:
        return [0] * count

    normalized_weights = [max(0, int(weight)) for weight in weights]
    if sum(normalized_weights) <= 0:
        normalized_weights = [1] * count

    weight_sum = sum(normalized_weights)
    allocations = [(normalized_total * weight) // weight_sum for weight in normalized_weights]
    remainder = normalized_total - sum(allocations)
    if remainder <= 0:
        return allocations

    ranked_indices = sorted(
        range(count),
        key=lambda index: (-((normalized_total * normalized_weights[index]) % weight_sum), index),
    )
    for index in ranked_indices[:remainder]:
        allocations[index] += 1
    return allocations


def _coerce_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
