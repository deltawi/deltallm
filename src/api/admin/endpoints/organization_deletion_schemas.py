from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models.organization_lifecycle import OrganizationLifecycleState

OrganizationDeletionStatus = Literal[
    "pending",
    "processing",
    "waiting",
    "completed",
    "failed",
    "restored",
]
OrganizationDeletionPhase = Literal[
    "cancel_pending",
    "cancel_batches",
    "wait_for_batches",
    "resolve_owned_assets",
    "purge_sensitive_history",
    "remove_scoped_access",
    "revoke_credentials",
    "remove_tenant_state",
    "finalize",
    "completed",
    "restored",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationDeletionOptions(_StrictModel):
    owned_mcp_servers: Literal["delete"] = "delete"
    owned_prompt_templates: Literal["delete"] = "delete"
    owned_route_groups: Literal["delete"] = "delete"


class OrganizationDeletionRequest(_StrictModel):
    confirmation_name: str = Field(min_length=1, max_length=256)
    plan_token: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    acknowledge_running_work_cancellation: bool
    options: OrganizationDeletionOptions = Field(default_factory=OrganizationDeletionOptions)


class OrganizationDeletionCountsResponse(BaseModel):
    teams: int = Field(ge=0)
    api_keys: int = Field(ge=0)
    service_accounts: int = Field(ge=0)
    organization_memberships: int = Field(ge=0)
    team_memberships: int = Field(ge=0)
    pending_invitations: int = Field(ge=0)
    pending_mcp_approvals: int = Field(ge=0)
    scope_bindings: int = Field(ge=0)
    owned_mcp_servers: int = Field(ge=0)
    owned_prompt_templates: int = Field(ge=0)
    owned_route_groups: int = Field(ge=0)
    external_mcp_dependencies: int = Field(ge=0)
    external_prompt_dependencies: int = Field(ge=0)
    external_route_group_dependencies: int = Field(ge=0)
    prompt_render_logs: int = Field(ge=0)
    ambiguous_sensitive_records: int = Field(ge=0)
    conflicting_sensitive_records: int = Field(ge=0)
    unattributed_sensitive_records: int = Field(ge=0)
    active_batches: int = Field(ge=0)
    staged_batch_sessions: int = Field(ge=0)
    unresolved_batch_ownership_records: int = Field(ge=0)
    retained_spend_events: int = Field(ge=0)
    retained_audit_events: int = Field(ge=0)
    retained_batch_jobs: int = Field(ge=0)
    retained_batch_files: int = Field(ge=0)


class OrganizationDeletionPlanResponse(BaseModel):
    organization_id: str
    organization_name: str | None
    lifecycle_state: OrganizationLifecycleState
    lifecycle_version: int
    deletion_job_id: str | None
    deletion_requested_at: datetime | None
    deletion_not_before_at: datetime | None
    counts: OrganizationDeletionCountsResponse
    automatic_cleanup: tuple[str, ...]
    retained_history: tuple[str, ...]
    cancellation_effects: tuple[str, ...]
    blocking_dependencies: tuple[str, ...]
    recovery_window_hours: int = Field(ge=1)
    lifecycle_protocol_version: int = Field(ge=1)
    requests_enabled: bool
    can_request: bool
    plan_token: str


class OrganizationDeletionJobResponse(BaseModel):
    deletion_job_id: str
    organization_id: str
    status: OrganizationDeletionStatus
    phase: OrganizationDeletionPhase
    progress: dict[str, object]
    not_before_at: datetime | None
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    last_error_code: str | None
    last_error_detail: str | None
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None
    restored_at: datetime | None
    restore_allowed: bool
    immediate_invalidation_succeeded: bool | None = None
