from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.models.organization_lifecycle import OrganizationLifecycleState


class OrganizationCapabilitiesResponse(BaseModel):
    view: bool = True
    edit: bool = False
    add_team: bool = False
    manage_members: bool = False
    manage_assets: bool = False
    manage_service_policy: bool = False
    view_usage: bool = False


class OrganizationResponse(BaseModel):
    """Typed core of the organization UI contract; extension fields remain compatible."""

    model_config = ConfigDict(extra="allow")

    organization_id: str = Field(min_length=1)
    organization_name: str | None = None
    lifecycle_state: OrganizationLifecycleState | None
    deletion_requested_at: str | None = None
    deletion_not_before_at: str | None = None
    max_budget: float | None = None
    soft_budget: float | None = None
    spend: float | None = None
    budget_duration: str | None = None
    budget_reset_at: str | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    rph_limit: int | None = None
    rpd_limit: int | None = None
    tpd_limit: int | None = None
    model_rpm_limit: dict[str, int] | None = None
    model_tpm_limit: dict[str, int] | None = None
    audit_content_storage_enabled: bool | None = None
    metadata: dict[str, Any] | None = None
    service_policy: dict[str, Any]
    capabilities: OrganizationCapabilitiesResponse = Field(
        default_factory=OrganizationCapabilitiesResponse
    )
    team_count: int | None = Field(default=None, ge=0)
    member_count: int | None = Field(default=None, ge=0)
    user_count: int | None = Field(default=None, ge=0)
    created_at: str | None = None
    updated_at: str | None = None


class OrganizationListItemResponse(OrganizationResponse):
    """Organization fields whose aggregates are authoritative on list responses."""

    team_count: int = Field(ge=0)
    member_count: int = Field(ge=0)


class OrganizationPaginationResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool


class OrganizationListResponse(BaseModel):
    data: list[OrganizationListItemResponse]
    pagination: OrganizationPaginationResponse


__all__ = [
    "OrganizationCapabilitiesResponse",
    "OrganizationListItemResponse",
    "OrganizationListResponse",
    "OrganizationPaginationResponse",
    "OrganizationResponse",
]
