from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit
from src.api.admin.endpoints.organization_tier_assignment_schemas import (
    OrganizationTierAssignmentCreateRequest,
    OrganizationTierAssignmentPatchRequest,
)
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.services.tier_admin_errors import (
    TierAdminConflictError,
    TierAdminError,
    TierAdminNotFoundError,
    TierAdminUnavailableError,
    TierAdminValidationError,
)
from src.services.tier_assignment_admin import TierAssignmentAdminService
from src.services.tier_assignment_admin_serialization import serialize_tier_assignment
from src.services.tier_policy_invalidation import reload_tier_policy

router = APIRouter(tags=["Admin Organization Tier Assignments"])
_PLATFORM_ADMIN_DEPENDENCY = [Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]


def _assignment_service(request: Request) -> TierAssignmentAdminService:
    repository = getattr(request.app.state, "tier_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tier repository unavailable",
        )
    cache_invalidation_service = getattr(request.app.state, "cache_invalidation_service", None)
    return TierAssignmentAdminService(
        repository,
        cache_invalidation_service=cache_invalidation_service,
        cache_invalidation_max_attempts=int(
            getattr(cache_invalidation_service, "max_attempts", 10) or 10
        ),
    )


def _http_error(exc: TierAdminError) -> HTTPException:
    if isinstance(exc, TierAdminNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, TierAdminConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(exc, TierAdminValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, TierAdminUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=status_code, detail=exc.detail)


def _payload(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="python", exclude_unset=True)


async def _reload_tier_policy_for_audit(request: Request) -> dict[str, Any]:
    return (await reload_tier_policy(request)).to_dict()


@router.get(
    "/ui/api/organizations/{organization_id}/tier-assignments",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def list_organization_tier_assignments(
    request: Request,
    organization_id: str,
    enabled: bool | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return await _assignment_service(request).list_assignments(
            organization_id=organization_id,
            enabled=enabled,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/ui/api/organizations/{organization_id}/tier-assignments",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def create_organization_tier_assignment(
    request: Request,
    organization_id: str,
    payload: OrganizationTierAssignmentCreateRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    request_payload = _payload(payload)
    try:
        result = await _assignment_service(request).create_assignment_with_cache_invalidation(
            organization_id=organization_id,
            payload=request_payload,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    created = result.assignment
    response = serialize_tier_assignment(created)
    cache_invalidation_payload = result.cache_invalidation.to_dict()
    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ORGANIZATION_TIER_ASSIGNMENT_CREATE,
        organization_id=created.organization_id,
        resource_type="organization_tier_assignment",
        resource_id=created.assignment_id,
        request_payload={"organization_id": organization_id, **request_payload},
        response_payload={
            **response,
            "cache_invalidation": cache_invalidation_payload,
            "tier_policy_invalidation": tier_policy_invalidation,
        },
        before=None,
        after=response,
        metadata={
            "cache_invalidation": cache_invalidation_payload,
            "tier_policy_invalidation": tier_policy_invalidation,
        },
    )
    return response


@router.patch(
    "/ui/api/organizations/{organization_id}/tier-assignments/{assignment_id}",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def update_organization_tier_assignment(
    request: Request,
    organization_id: str,
    assignment_id: str,
    payload: OrganizationTierAssignmentPatchRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    request_payload = _payload(payload)
    service = _assignment_service(request)
    try:
        result = await service.update_assignment_with_cache_invalidation(
            organization_id=organization_id,
            assignment_id=assignment_id,
            payload=request_payload,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    before = serialize_tier_assignment(result.before)
    updated = result.assignment
    response = serialize_tier_assignment(updated)
    cache_invalidation_payload = result.cache_invalidation.to_dict()
    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ORGANIZATION_TIER_ASSIGNMENT_UPDATE,
        organization_id=updated.organization_id,
        resource_type="organization_tier_assignment",
        resource_id=assignment_id,
        request_payload={"organization_id": organization_id, **request_payload},
        response_payload={
            **response,
            "cache_invalidation": cache_invalidation_payload,
            "tier_policy_invalidation": tier_policy_invalidation,
        },
        before=before,
        after=response,
        metadata={
            "cache_invalidation": cache_invalidation_payload,
            "tier_policy_invalidation": tier_policy_invalidation,
        },
    )
    return response


@router.delete(
    "/ui/api/organizations/{organization_id}/tier-assignments/{assignment_id}",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def delete_organization_tier_assignment(
    request: Request,
    organization_id: str,
    assignment_id: str,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _assignment_service(request)
    try:
        result = await service.delete_assignment_with_cache_invalidation(
            organization_id=organization_id,
            assignment_id=assignment_id,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    before = serialize_tier_assignment(result.before)
    response = result.response
    cache_invalidation_payload = result.cache_invalidation.to_dict()
    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ORGANIZATION_TIER_ASSIGNMENT_DELETE,
        organization_id=response["organization_id"],
        resource_type="organization_tier_assignment",
        resource_id=assignment_id,
        response_payload={
            **response,
            "cache_invalidation": cache_invalidation_payload,
            "tier_policy_invalidation": tier_policy_invalidation,
        },
        before=before,
        after=response,
        metadata={
            "cache_invalidation": cache_invalidation_payload,
            "tier_policy_invalidation": tier_policy_invalidation,
        },
    )
    return response
