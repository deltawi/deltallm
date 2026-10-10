from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, field_validator


class ExternalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ExternalIntegrationRecord(ExternalRecord):
    integration_id: str
    enabled: StrictBool
    epoch: StrictInt
    version: StrictInt


class ExternalBindingRecord(ExternalRecord):
    binding_id: str
    integration_id: str
    external_customer_id: str
    organization_id: str
    team_id: str
    profile: Literal["customer_v1"]
    state: Literal["active", "suspended"]
    epoch: StrictInt
    version: StrictInt


class ExternalSubjectRecord(ExternalRecord):
    subject_id: str
    integration_id: str
    binding_id: str
    identity_issuer: str
    subject: str
    state: Literal["pending", "active", "suspended"]
    epoch: StrictInt
    version: StrictInt
    account_id: str | None
    identity_id: str | None
    runtime_user_id: str | None


class ExternalRegisteredWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    integration: ExternalIntegrationRecord
    binding: ExternalBindingRecord


class ExternalParentRecord(ExternalRecord):
    parent_id: str
    integration_id: str
    external_session_id_hash: str
    subject_id: str
    auth_time: datetime
    expires_at: datetime
    revoked_at: datetime | None
    generation: StrictInt

    @field_validator("auth_time", "expires_at", "revoked_at", mode="after")
    @classmethod
    def utc_parent_times(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
