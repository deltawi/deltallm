from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.services.tier_admin_errors import TierAdminError, TierAdminNotFoundError
from src.services.tier_assignment_admin import TierAssignmentAdminService
from src.services.tier_policy_preview import (
    TierPolicyPreviewError,
    TierPolicyPreviewUnavailableError,
    build_tier_policy_preview,
    simulate_tier_policy_request,
)

router = APIRouter(tags=["Admin Tier Policy Preview"])
_PLATFORM_ADMIN_DEPENDENCY = [Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]


def _assignment_service(request: Request) -> TierAssignmentAdminService:
    repository = getattr(request.app.state, "tier_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tier repository unavailable",
        )
    return TierAssignmentAdminService(repository)


def _tier_policy_service(request: Request) -> Any:
    return getattr(request.app.state, "tier_policy_service", None)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TierPolicyPreviewUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.detail,
        )
    if isinstance(exc, TierPolicyPreviewError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail)
    if isinstance(exc, TierAdminNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail)
    if isinstance(exc, TierAdminError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get(
    "/ui/api/organizations/{organization_id}/tier-policy-preview",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def get_organization_tier_policy_preview(
    request: Request,
    organization_id: str,
) -> dict[str, Any]:
    try:
        assignments_response = await _assignment_service(request).list_assignments(
            organization_id=organization_id,
            enabled=None,
        )
        return build_tier_policy_preview(
            organization_id=organization_id,
            tier_policy_service=_tier_policy_service(request),
            assignments=assignments_response.get("data", ()),
        )
    except (TierPolicyPreviewError, TierAdminError) as exc:
        raise _http_error(exc) from exc


@router.post(
    "/ui/api/organizations/{organization_id}/tier-policy/simulate",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def simulate_organization_tier_policy(
    request: Request,
    organization_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    callable_key = body.get("callable_key")
    if not callable_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="callable_key is required",
        )
    try:
        validated_organization_id = await _assignment_service(request).require_organization(
            organization_id,
        )
        return simulate_tier_policy_request(
            organization_id=validated_organization_id,
            callable_key=str(callable_key),
            tier_policy_service=_tier_policy_service(request),
            mode=str(body.get("mode") or "sync"),
            request_count=body.get("request_count", 1),
            prompt_tokens=body.get("prompt_tokens", 0),
            completion_tokens=body.get("completion_tokens", 0),
        )
    except (TierPolicyPreviewError, TierAdminError) as exc:
        raise _http_error(exc) from exc
