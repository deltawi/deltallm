from __future__ import annotations

from collections.abc import Mapping
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
