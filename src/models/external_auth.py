from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ExternalWorkspaceContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    integration_id: str
    binding_id: str
    subject_id: str
    organization_id: str
    team_id: str
    inference_user_id: str
    parent_id: str
    generation: int


class ExternalWorkspaceResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    integration_id: str
    binding_id: str
    organization_id: str
    team_id: str
    inference_user_id: str


class ExternalAuthDiagnostics(BaseModel):
    protocol: Literal["external_customer_v1"]
    state: Literal["disabled", "degraded", "ready"]
    cleanup_healthy: bool
    crypto_ready: bool
    cache_worker_ready: bool
    active_mutations: int
    queued_mutations: int
    active_validations: int
    queued_validations: int


class ExternalAssertionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    assertion: SecretStr = Field(min_length=1, max_length=8192)


class ExternalExchangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_token: str = Field(repr=False)
    session_generation: int
    account_id: str
    organization_id: str
    team_id: str
    inference_user_id: str
    binding_id: str
    expires_at: datetime
    refresh_after_seconds: int
    next_step: Literal["ready", "mfa_verify", "password_change"]
    mfa_required: bool


class ExternalVersionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    expected_version: int = Field(ge=0, le=2147483646)
    reason: str = Field(min_length=1, max_length=200)


class ExternalIntegrationRequest(ExternalVersionRequest):
    enabled: bool


class ExternalBindingRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    organization_id: str = Field(min_length=1, max_length=200)
    team_id: str = Field(min_length=1, max_length=200)


class ExternalLinkRequest(ExternalAssertionRequest):
    account_id: str = Field(min_length=1, max_length=200)
    runtime_user_id: str = Field(min_length=1, max_length=200)
    binding_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0, le=2147483646)
    approval_reference: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=200)


class ExternalRuntimeBindingRequest(ExternalAssertionRequest):
    runtime_user_id: str = Field(min_length=1, max_length=200)
    binding_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=0, le=2147483646)
    approval_reference: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=200)


class ExternalInferenceKeyRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    api_key: SecretStr = Field(min_length=1, max_length=256)


class ExternalInferenceKeyResponse(BaseModel):
    key_name: str | None = None
    expires_at: datetime | None = None
    account_id: str
    inference_user_id: str
    team_id: str
