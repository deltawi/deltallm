from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ModelMutationResponse(BaseModel):
    deployment_id: str
    model_name: str
    provider: str
    mode: str
    credential_source: str
    inline_credentials_present: bool
    connection_summary: dict[str, Any]
    named_credential_id: str | None
    named_credential_name: str | None
    deltallm_params: dict[str, Any]
    model_info: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class ModelDeleteResponse(BaseModel):
    deleted: bool
    warnings: list[str] = Field(default_factory=list)
