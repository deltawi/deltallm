from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator


class KeyCreateRequest(BaseModel):
    # Keep the existing creation fields until the full key DTO is migrated.
    model_config = ConfigDict(extra="allow")
    key: SecretStr | None = None

    @model_validator(mode="before")
    @classmethod
    def require_object_body(cls, value: object) -> object:
        if not isinstance(value, dict):
            # The default model error includes the full submitted body.
            raise HTTPException(status_code=400, detail="Key creation requires a JSON object")
        return value

    @field_validator("key", mode="before")
    @classmethod
    def require_string_key(cls, value: object) -> object:
        if value is not None and not isinstance(value, str):
            # Validation errors normally echo their input. Secrets must not.
            raise HTTPException(status_code=400, detail="key must be a string or null")
        return value
