from __future__ import annotations

from typing import Annotated, TypeAlias

from pydantic import Field, TypeAdapter

from src.models.errors import InvalidRequestError


MAX_OUTPUT_TOKENS = 2**31 - 1
OutputTokenLimit: TypeAlias = Annotated[int, Field(strict=True, ge=1, le=MAX_OUTPUT_TOKENS)]
_OUTPUT_LIMIT_ADAPTER = TypeAdapter(OutputTokenLimit | None)


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
