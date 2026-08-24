from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


ORGANIZATION_LIFECYCLE_STATES = frozenset(
    {"active", "deletion_pending", "purging", "deletion_failed"}
)
ORGANIZATION_DELETION_JOB_STATUSES = frozenset(
    {"pending", "processing", "waiting", "completed", "failed", "restored"}
)
ORGANIZATION_DELETION_PHASES = (
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
)

ORGANIZATION_DELETION_JOB_COLUMNS = """
    deletion_job_id,
    organization_id,
    status,
    phase,
    requested_by_account_id,
    idempotency_key,
    request_hash,
    plan_token,
    plan_snapshot,
    options,
    progress,
    not_before_at,
    attempt_count,
    max_attempts,
    next_attempt_at,
    locked_by,
    lease_expires_at,
    claim_epoch,
    last_error_code,
    last_error_detail,
    created_at,
    updated_at,
    completed_at,
    restored_at
"""

ORGANIZATION_DELETION_JOB_COLUMNS_FROM_JOB_ALIAS = """
    j.deletion_job_id,
    j.organization_id,
    j.status,
    j.phase,
    j.requested_by_account_id,
    j.idempotency_key,
    j.request_hash,
    j.plan_token,
    j.plan_snapshot,
    j.options,
    j.progress,
    j.not_before_at,
    j.attempt_count,
    j.max_attempts,
    j.next_attempt_at,
    j.locked_by,
    j.lease_expires_at,
    j.claim_epoch,
    j.last_error_code,
    j.last_error_detail,
    j.created_at,
    j.updated_at,
    j.completed_at,
    j.restored_at
"""


def parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def parse_json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        if isinstance(parsed, dict):
            return {str(key): item for key, item in parsed.items()}
    return {}


@dataclass(frozen=True)
class OrganizationDeletionCounts:
    teams: int = 0
    api_keys: int = 0
    service_accounts: int = 0
    organization_memberships: int = 0
    team_memberships: int = 0
    pending_invitations: int = 0
    pending_mcp_approvals: int = 0
    scope_bindings: int = 0
    owned_mcp_servers: int = 0
    owned_prompt_templates: int = 0
    owned_route_groups: int = 0
    external_mcp_dependencies: int = 0
    external_prompt_dependencies: int = 0
    external_route_group_dependencies: int = 0
    prompt_render_logs: int = 0
    ambiguous_sensitive_records: int = 0
    conflicting_sensitive_records: int = 0
    unattributed_sensitive_records: int = 0
    active_batches: int = 0
    staged_batch_sessions: int = 0
    unresolved_batch_ownership_records: int = 0
    retained_spend_events: int = 0
    retained_audit_events: int = 0
    retained_batch_jobs: int = 0
    retained_batch_files: int = 0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> OrganizationDeletionCounts:
        return cls(**{name: max(0, int(row.get(name) or 0)) for name in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__dataclass_fields__}

    @property
    def has_blocking_dependencies(self) -> bool:
        return (
            self.external_mcp_dependencies > 0
            or self.external_prompt_dependencies > 0
            or self.external_route_group_dependencies > 0
            or self.ambiguous_sensitive_records > 0
            or self.conflicting_sensitive_records > 0
            or self.unattributed_sensitive_records > 0
            or self.unresolved_batch_ownership_records > 0
        )


@dataclass(frozen=True)
class OrganizationDeletionPlanRecord:
    organization_id: str
    organization_name: str | None
    lifecycle_state: str
    lifecycle_version: int
    deletion_requested_at: datetime | None
    deletion_not_before_at: datetime | None
    deletion_job_id: str | None
    counts: OrganizationDeletionCounts

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> OrganizationDeletionPlanRecord:
        return cls(
            organization_id=str(row.get("organization_id") or ""),
            organization_name=(
                str(row["organization_name"]) if row.get("organization_name") is not None else None
            ),
            lifecycle_state=str(row.get("lifecycle_state") or "active"),
            lifecycle_version=int(row.get("lifecycle_version") or 0),
            deletion_requested_at=parse_datetime(row.get("deletion_requested_at")),
            deletion_not_before_at=parse_datetime(row.get("deletion_not_before_at")),
            deletion_job_id=(
                str(row["deletion_job_id"]) if row.get("deletion_job_id") is not None else None
            ),
            counts=OrganizationDeletionCounts.from_row(row),
        )


@dataclass(frozen=True)
class OrganizationDeletionJobRecord:
    deletion_job_id: str
    organization_id: str
    status: str
    phase: str
    requested_by_account_id: str | None
    idempotency_key: str
    request_hash: str
    plan_token: str
    plan_snapshot: dict[str, object] = field(default_factory=dict)
    options: dict[str, object] = field(default_factory=dict)
    progress: dict[str, object] = field(default_factory=dict)
    not_before_at: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 20
    next_attempt_at: datetime | None = None
    locked_by: str | None = None
    lease_expires_at: datetime | None = None
    claim_epoch: int = 0
    last_error_code: str | None = None
    last_error_detail: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    restored_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> OrganizationDeletionJobRecord:
        optional_text = (
            "requested_by_account_id",
            "locked_by",
            "last_error_code",
            "last_error_detail",
        )
        values: dict[str, object] = {
            name: str(row[name]) if row.get(name) is not None else None for name in optional_text
        }
        values.update(
            deletion_job_id=str(row.get("deletion_job_id") or ""),
            organization_id=str(row.get("organization_id") or ""),
            status=str(row.get("status") or ""),
            phase=str(row.get("phase") or ""),
            idempotency_key=str(row.get("idempotency_key") or ""),
            request_hash=str(row.get("request_hash") or ""),
            plan_token=str(row.get("plan_token") or ""),
            plan_snapshot=parse_json_object(row.get("plan_snapshot")),
            options=parse_json_object(row.get("options")),
            progress=parse_json_object(row.get("progress")),
            not_before_at=parse_datetime(row.get("not_before_at")),
            attempt_count=int(row.get("attempt_count") or 0),
            max_attempts=int(row.get("max_attempts") or 20),
            next_attempt_at=parse_datetime(row.get("next_attempt_at")),
            lease_expires_at=parse_datetime(row.get("lease_expires_at")),
            claim_epoch=int(row.get("claim_epoch") or 0),
            created_at=parse_datetime(row.get("created_at")),
            updated_at=parse_datetime(row.get("updated_at")),
            completed_at=parse_datetime(row.get("completed_at")),
            restored_at=parse_datetime(row.get("restored_at")),
        )
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class OrganizationDeletionFinalizationResult:
    outcome: Literal["completed", "retry_cleanup", "blocked", "claim_lost"]
    next_phase: str | None = None
    error_code: str | None = None

    @classmethod
    def completed(cls) -> OrganizationDeletionFinalizationResult:
        return cls(outcome="completed")

    @classmethod
    def retry_cleanup(cls, next_phase: str) -> OrganizationDeletionFinalizationResult:
        return cls(outcome="retry_cleanup", next_phase=next_phase)

    @classmethod
    def blocked(cls, error_code: str) -> OrganizationDeletionFinalizationResult:
        return cls(outcome="blocked", error_code=error_code)

    @classmethod
    def claim_lost(cls) -> OrganizationDeletionFinalizationResult:
        return cls(outcome="claim_lost")


__all__ = [
    "ORGANIZATION_DELETION_JOB_STATUSES",
    "ORGANIZATION_DELETION_JOB_COLUMNS",
    "ORGANIZATION_DELETION_JOB_COLUMNS_FROM_JOB_ALIAS",
    "ORGANIZATION_DELETION_PHASES",
    "ORGANIZATION_LIFECYCLE_STATES",
    "OrganizationDeletionCounts",
    "OrganizationDeletionFinalizationResult",
    "OrganizationDeletionJobRecord",
    "OrganizationDeletionPlanRecord",
]
