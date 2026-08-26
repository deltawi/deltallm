from __future__ import annotations

import hashlib
import json

from src.db.organization_deletion_records import OrganizationDeletionPlanRecord
from src.models.organization_lifecycle import ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION


# These counts describe the destructive scope the operator confirmed. Operational
# work and retained telemetry are deliberately excluded: they naturally change
# while the confirmation dialog is open and are re-evaluated under the lifecycle
# transaction before the request is accepted.
_CONFIRMED_SCOPE_COUNT_FIELDS = (
    "teams",
    "api_keys",
    "service_accounts",
    "organization_memberships",
    "team_memberships",
    "pending_invitations",
    "pending_mcp_approvals",
    "scope_bindings",
    "owned_mcp_servers",
    "owned_prompt_templates",
    "owned_route_groups",
    "external_mcp_dependencies",
    "external_prompt_dependencies",
    "external_route_group_dependencies",
    "prompt_render_logs",
    "ambiguous_sensitive_records",
    "conflicting_sensitive_records",
    "unattributed_sensitive_records",
    "unresolved_batch_ownership_records",
)


def build_deletion_plan_token(record: OrganizationDeletionPlanRecord) -> str:
    payload = {
        "protocol_version": ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION,
        "organization_id": record.organization_id,
        "organization_name": record.organization_name,
        "lifecycle_state": record.lifecycle_state,
        "lifecycle_version": record.lifecycle_version,
        "confirmed_scope_counts": {
            field: int(getattr(record.counts, field)) for field in _CONFIRMED_SCOPE_COUNT_FIELDS
        },
    }
    return _digest(payload)


def build_deletion_plan_snapshot(
    record: OrganizationDeletionPlanRecord,
) -> dict[str, object]:
    return {
        "protocol_version": ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION,
        "organization_id": record.organization_id,
        "lifecycle_version": record.lifecycle_version,
        "counts": record.counts.to_dict(),
    }


def build_deletion_request_hash(
    *,
    organization_id: str,
    confirmation_name: str,
    plan_token: str,
    options: dict[str, object],
) -> str:
    return _digest(
        {
            "organization_id": organization_id,
            "confirmation_name": confirmation_name,
            "plan_token": plan_token,
            "options": options,
        }
    )


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "build_deletion_plan_snapshot",
    "build_deletion_plan_token",
    "build_deletion_request_hash",
]
