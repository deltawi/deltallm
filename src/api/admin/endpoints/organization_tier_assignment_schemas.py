from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationTierAssignmentCreateRequest(_StrictRequest):
    tier_id: str
    tier_version_id: str | None = None
    assignment_type: str = "primary"
    enabled: bool = True
    weight: int = Field(default=1, ge=1)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class OrganizationTierAssignmentPatchRequest(_StrictRequest):
    tier_id: str | None = None
    tier_version_id: str | None = None
    assignment_type: str | None = None
    enabled: bool | None = None
    weight: int | None = Field(default=None, ge=1)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    metadata: dict[str, Any] | None = None
