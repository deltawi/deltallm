from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.api.admin.endpoints.organization_deletion_schemas import (
    OrganizationDeletionJobResponse,
    OrganizationDeletionPlanResponse,
    OrganizationDeletionRequest,
)
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.middleware.platform_auth import get_platform_auth_context
from src.db.organization_deletion_records import OrganizationDeletionJobRecord
from src.models.organization_lifecycle import ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION
from src.services.organization_deletion import (
    OrganizationDeletionConflictError,
    OrganizationDeletionError,
    OrganizationDeletionNotFoundError,
    OrganizationDeletionService,
    OrganizationDeletionUnavailableError,
    OrganizationDeletionValidationError,
)
from src.services.organization_deletion_types import OrganizationDeletionPlan

router = APIRouter(tags=["Admin Organization Deletion"])
_PLATFORM_ADMIN_DEPENDENCY = [Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]
_RESTORABLE_PHASES = frozenset({"cancel_pending", "cancel_batches", "wait_for_batches"})


def _service(request: Request) -> OrganizationDeletionService:
    service = getattr(request.app.state, "organization_deletion_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "organization_deletion_unavailable",
                "message": "Organization deletion service unavailable",
            },
        )
    return cast(OrganizationDeletionService, service)


def _http_error(exc: OrganizationDeletionError) -> HTTPException:
    if isinstance(exc, OrganizationDeletionNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, OrganizationDeletionConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(exc, OrganizationDeletionValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, OrganizationDeletionUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _plan_response(plan: OrganizationDeletionPlan) -> OrganizationDeletionPlanResponse:
    record = plan.record
    return OrganizationDeletionPlanResponse(
        organization_id=record.organization_id,
        organization_name=record.organization_name,
        lifecycle_state=record.lifecycle_state,
        lifecycle_version=record.lifecycle_version,
        deletion_job_id=record.deletion_job_id,
        deletion_requested_at=record.deletion_requested_at,
        deletion_not_before_at=record.deletion_not_before_at,
        counts=record.counts.to_dict(),
        automatic_cleanup=(
            "api_keys",
            "service_accounts",
            "teams",
            "memberships",
            "pending_invitations",
            "pending_mcp_approvals",
            "scope_bindings",
            "owned_mcp_servers",
            "owned_prompt_templates",
            "owned_route_groups",
            "prompt_render_logs",
        ),
        retained_history=(
            "spend_events",
            "audit_events",
            "terminal_batch_records",
            "batch_files_until_expiry",
        ),
        cancellation_effects=(
            "active_batches_will_be_cancelled",
            "staged_batch_sessions_will_expire",
            "restoration_does_not_restart_cancelled_work",
        ),
        blocking_dependencies=tuple(
            dependency
            for dependency, count in (
                ("external_mcp_dependencies", record.counts.external_mcp_dependencies),
                ("external_prompt_dependencies", record.counts.external_prompt_dependencies),
                (
                    "external_route_group_dependencies",
                    record.counts.external_route_group_dependencies,
                ),
                (
                    "conflicting_sensitive_records",
                    record.counts.conflicting_sensitive_records,
                ),
                (
                    "unattributed_sensitive_records",
                    record.counts.unattributed_sensitive_records,
                ),
                (
                    "unresolved_batch_ownership_records",
                    record.counts.unresolved_batch_ownership_records,
                ),
            )
            if count > 0
        ),
        recovery_window_hours=plan.recovery_window_hours,
        lifecycle_protocol_version=ORGANIZATION_LIFECYCLE_PROTOCOL_VERSION,
        requests_enabled=plan.requests_enabled,
        can_request=plan.can_request,
        plan_token=plan.plan_token,
    )


def _job_response(
    job: OrganizationDeletionJobRecord,
    *,
    immediate_invalidation_succeeded: bool | None = None,
) -> OrganizationDeletionJobResponse:
    now = datetime.now(tz=UTC)
    restore_allowed = bool(
        job.status in {"pending", "processing", "waiting", "failed"}
        and job.phase in _RESTORABLE_PHASES
        and job.not_before_at is not None
        and now < job.not_before_at
    )
    return OrganizationDeletionJobResponse(
        deletion_job_id=job.deletion_job_id,
        organization_id=job.organization_id,
        status=job.status,
        phase=job.phase,
        progress=job.progress,
        not_before_at=job.not_before_at,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        last_error_code=job.last_error_code,
        last_error_detail=job.last_error_detail,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        restored_at=job.restored_at,
        restore_allowed=restore_allowed,
        immediate_invalidation_succeeded=immediate_invalidation_succeeded,
    )


@router.get(
    "/ui/api/organizations/{organization_id}/deletion-plan",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
    response_model=OrganizationDeletionPlanResponse,
)
async def get_organization_deletion_plan(
    request: Request,
    organization_id: str,
) -> OrganizationDeletionPlanResponse:
    try:
        plan = await _service(request).preview(organization_id)
    except OrganizationDeletionError as exc:
        raise _http_error(exc) from exc
    return _plan_response(plan)


@router.post(
    "/ui/api/organizations/{organization_id}/deletion-requests",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
    response_model=OrganizationDeletionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_organization_deletion(
    request: Request,
    organization_id: str,
    payload: OrganizationDeletionRequest,
    idempotency_key: str = Header(
        min_length=1,
        max_length=200,
        alias="Idempotency-Key",
    ),
) -> OrganizationDeletionJobResponse:
    if not payload.acknowledge_running_work_cancellation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "organization_deletion_acknowledgement_required",
                "message": "Running-work cancellation must be acknowledged",
            },
        )
    platform_context = get_platform_auth_context(request)
    requested_by_account_id = platform_context.account_id if platform_context is not None else None
    try:
        result = await _service(request).request_deletion(
            organization_id=organization_id,
            confirmation_name=payload.confirmation_name,
            plan_token=payload.plan_token,
            idempotency_key=idempotency_key,
            requested_by_account_id=requested_by_account_id,
            options=payload.options.model_dump(mode="python"),
        )
    except OrganizationDeletionError as exc:
        raise _http_error(exc) from exc
    response = _job_response(
        result.job,
        immediate_invalidation_succeeded=result.immediate_invalidation_succeeded,
    )
    return response


@router.get(
    "/ui/api/organizations/{organization_id}/deletion-requests/{deletion_job_id}",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
    response_model=OrganizationDeletionJobResponse,
)
async def get_organization_deletion_request(
    request: Request,
    organization_id: str,
    deletion_job_id: str,
) -> OrganizationDeletionJobResponse:
    try:
        job = await _service(request).get_job(
            organization_id=organization_id,
            deletion_job_id=deletion_job_id,
        )
    except OrganizationDeletionError as exc:
        raise _http_error(exc) from exc
    return _job_response(job)


@router.post(
    "/ui/api/organizations/{organization_id}/deletion-requests/{deletion_job_id}/restore",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
    response_model=OrganizationDeletionJobResponse,
)
async def restore_organization_deletion_request(
    request: Request,
    organization_id: str,
    deletion_job_id: str,
) -> OrganizationDeletionJobResponse:
    platform_context = get_platform_auth_context(request)
    try:
        result = await _service(request).restore(
            organization_id=organization_id,
            deletion_job_id=deletion_job_id,
            restored_by_account_id=(
                platform_context.account_id if platform_context is not None else None
            ),
        )
    except OrganizationDeletionError as exc:
        raise _http_error(exc) from exc

    response = _job_response(
        result.job,
        immediate_invalidation_succeeded=result.immediate_invalidation_succeeded,
    )
    return response


@router.post(
    "/ui/api/organizations/{organization_id}/deletion-requests/{deletion_job_id}/retry",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
    response_model=OrganizationDeletionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_organization_deletion_request(
    request: Request,
    organization_id: str,
    deletion_job_id: str,
) -> OrganizationDeletionJobResponse:
    platform_context = get_platform_auth_context(request)
    try:
        result = await _service(request).retry_failed(
            organization_id=organization_id,
            deletion_job_id=deletion_job_id,
            retried_by_account_id=(
                platform_context.account_id if platform_context is not None else None
            ),
        )
    except OrganizationDeletionError as exc:
        raise _http_error(exc) from exc
    return _job_response(
        result.job,
        immediate_invalidation_succeeded=result.immediate_invalidation_succeeded,
    )


__all__ = ["router"]
