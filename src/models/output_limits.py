from __future__ import annotations

from typing import Annotated, TypeAlias
import json

from pydantic import BeforeValidator, Field, TypeAdapter

from src.models.errors import InvalidRequestError


MAX_OUTPUT_TOKENS = 2**31 - 1
OutputTokenLimit: TypeAlias = Annotated[int, Field(strict=True, ge=1, le=MAX_OUTPUT_TOKENS)]
_OUTPUT_LIMIT_ADAPTER = TypeAdapter(OutputTokenLimit | None)

MAX_MODEL_OUTPUT_LIMITS = 64
MAX_MODEL_ID_BYTES = 256


def _validate_model_ids(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, dict) or len(value) > MAX_MODEL_OUTPUT_LIMITS:
        raise ValueError("model_output_tpm_limit must contain at most 64 model limits")
    for model in value:
        if (
            not isinstance(model, str)
            or not model.strip()
            or model != model.strip()
            or len(model.encode()) > MAX_MODEL_ID_BYTES
            or "*" in model
            or any(ord(char) < 32 or ord(char) == 127 for char in model)
        ):
            raise ValueError("model_output_tpm_limit requires exact model IDs up to 256 bytes")
    return value


ModelOutputTokenLimits: TypeAlias = Annotated[
    dict[str, OutputTokenLimit], BeforeValidator(_validate_model_ids), Field(max_length=64)
]
_MODEL_OUTPUT_ADAPTER = TypeAdapter(ModelOutputTokenLimits | None)


def validate_model_output_limits(value: object) -> dict[str, int] | None:
    from pydantic import ValidationError

    try:
        validated = _MODEL_OUTPUT_ADAPTER.validate_python(value)
        if validated and len(json.dumps(validated, ensure_ascii=False).encode()) > 32768:
            raise ValueError("model_output_tpm_limit exceeds 32 KiB")
        return validated or None
    except (ValidationError, ValueError) as exc:
        raise InvalidRequestError(
            message="model_output_tpm_limit requires at most 64 exact model IDs and positive integer limits",
            param="model_output_tpm_limit",
            code="invalid_model_output_tpm_limit",
        ) from exc


def validate_output_limit(value: object) -> int | None:
    from pydantic import ValidationError

    try:
        return _OUTPUT_LIMIT_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise InvalidRequestError(
            message="output_tpm_limit must be a positive integer or null",
            param="output_tpm_limit",
            code="invalid_output_tpm_limit",
        ) from exc
