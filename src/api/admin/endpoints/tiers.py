from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit
from src.api.admin.endpoints.tier_schemas import (
    TierCapacityPoolReplaceRequest,
    TierCreateRequest,
    TierModelPolicyReplaceRequest,
    TierPatchRequest,
    TierVersionCreateRequest,
)
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.middleware.platform_auth import get_platform_auth_context
from src.services.tier_admin import (
    TierAdminConflictError,
    TierAdminError,
    TierAdminNotFoundError,
    TierAdminService,
    TierAdminValidationError,
    serialize_capacity_pool,
    serialize_model_policy,
    serialize_tier,
    serialize_tier_version,
)

router = APIRouter(tags=["Admin Tiers"])
_PLATFORM_ADMIN_DEPENDENCY = [Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]


def _tier_service(request: Request) -> TierAdminService:
    repository = getattr(request.app.state, "tier_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tier repository unavailable",
        )
    return TierAdminService(repository)


def _http_error(exc: TierAdminError) -> HTTPException:
    if isinstance(exc, TierAdminNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, TierAdminConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(exc, TierAdminValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=status_code, detail=exc.detail)


def _payload(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="python", exclude_unset=True)


def _actor_account_id(request: Request) -> str | None:
    context = get_platform_auth_context(request)
    return str(context.account_id) if context is not None and context.account_id else None


@router.get("/ui/api/tiers", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def list_tiers(
    request: Request,
    search: str | None = Query(default=None, max_length=200),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return await _tier_service(request).list_tiers(
            search=search,
            enabled=enabled,
            limit=limit,
            offset=offset,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc


@router.post("/ui/api/tiers", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def create_tier(request: Request, payload: TierCreateRequest) -> dict[str, Any]:
    request_start = perf_counter()
    request_payload = _payload(payload)
    try:
        created = await _tier_service(request).create_tier(request_payload)
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = serialize_tier(created)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_CREATE,
        resource_type="tier",
        resource_id=created.tier_id,
        request_payload=request_payload,
        response_payload=response,
        before=None,
        after=response,
    )
    return response


@router.get("/ui/api/tiers/{tier_id}", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def get_tier(request: Request, tier_id: str) -> dict[str, Any]:
    try:
        return await _tier_service(request).get_tier_detail(tier_id)
    except TierAdminError as exc:
        raise _http_error(exc) from exc


@router.patch("/ui/api/tiers/{tier_id}", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def update_tier(
    request: Request,
    tier_id: str,
    payload: TierPatchRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    request_payload = _payload(payload)
    try:
        before = serialize_tier(await service.require_tier(tier_id))
        updated = await service.update_tier(tier_id, request_payload)
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = serialize_tier(updated)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_UPDATE,
        resource_type="tier",
        resource_id=tier_id,
        request_payload=request_payload,
        response_payload=response,
        before=before,
        after=response,
    )
    return response


@router.delete("/ui/api/tiers/{tier_id}", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def delete_tier(request: Request, tier_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    try:
        before = serialize_tier(await service.require_tier(tier_id))
        response = await service.delete_tier(tier_id)
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_DELETE,
        resource_type="tier",
        resource_id=tier_id,
        response_payload=response,
        before=before,
        after=response,
    )
    return response


@router.post("/ui/api/tiers/{tier_id}/versions", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def create_tier_version(
    request: Request,
    tier_id: str,
    payload: TierVersionCreateRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    request_payload = _payload(payload)
    try:
        created = await _tier_service(request).create_tier_version(tier_id, request_payload)
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = serialize_tier_version(created)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_VERSION_CREATE,
        resource_type="tier_version",
        resource_id=created.tier_version_id,
        request_payload={"tier_id": tier_id, **request_payload},
        response_payload=response,
        before=None,
        after=response,
    )
    return response


@router.get(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def get_tier_version(
    request: Request,
    tier_id: str,
    tier_version_id: str,
) -> dict[str, Any]:
    try:
        return await _tier_service(request).get_tier_version_detail(tier_id, tier_version_id)
    except TierAdminError as exc:
        raise _http_error(exc) from exc


@router.put(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/model-policies",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def replace_tier_model_policies(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    payload: TierModelPolicyReplaceRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    request_payload = _payload(payload)
    policies_payload = request_payload["policies"]
    before_detail: dict[str, Any] | None = None
    try:
        before_detail = await service.get_tier_version_detail(tier_id, tier_version_id)
        records = await service.replace_model_policies(
            tier_id,
            tier_version_id,
            policies_payload,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = {"data": [serialize_model_policy(record) for record in records]}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_MODEL_POLICIES_REPLACE,
        resource_type="tier_model_policy_set",
        resource_id=tier_version_id,
        request_payload={
            "tier_id": tier_id,
            "policy_count": len(policies_payload),
        },
        response_payload={"policy_count": len(response["data"])},
        before={"policy_count": len(before_detail["model_policies"]) if before_detail else 0},
        after={"policy_count": len(response["data"])},
    )
    return response


@router.put(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/capacity-pools",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def replace_tier_capacity_pools(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    payload: TierCapacityPoolReplaceRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    request_payload = _payload(payload)
    pools_payload = request_payload["pools"]
    before_detail: dict[str, Any] | None = None
    try:
        before_detail = await service.get_tier_version_detail(tier_id, tier_version_id)
        records = await service.replace_capacity_pools(
            tier_id,
            tier_version_id,
            pools_payload,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = {"data": [serialize_capacity_pool(record) for record in records]}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_CAPACITY_POOLS_REPLACE,
        resource_type="tier_capacity_pool_set",
        resource_id=tier_version_id,
        request_payload={"tier_id": tier_id, "pool_count": len(pools_payload)},
        response_payload={"pool_count": len(response["data"])},
        before={"pool_count": len(before_detail["capacity_pools"]) if before_detail else 0},
        after={"pool_count": len(response["data"])},
    )
    return response


@router.post(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/publish",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def publish_tier_version(
    request: Request,
    tier_id: str,
    tier_version_id: str,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    try:
        before = serialize_tier_version(
            await service.require_version_for_tier(tier_id, tier_version_id)
        )
        published = await service.publish_tier_version(
            tier_id,
            tier_version_id,
            published_by_account_id=_actor_account_id(request),
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = serialize_tier_version(published)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_VERSION_PUBLISH,
        resource_type="tier_version",
        resource_id=tier_version_id,
        request_payload={"tier_id": tier_id},
        response_payload=response,
        before=before,
        after=response,
    )
    return response


@router.post(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/archive",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def archive_tier_version(
    request: Request,
    tier_id: str,
    tier_version_id: str,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    try:
        before = serialize_tier_version(
            await service.require_version_for_tier(tier_id, tier_version_id)
        )
        archived = await service.archive_tier_version(tier_id, tier_version_id)
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = serialize_tier_version(archived)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_VERSION_ARCHIVE,
        resource_type="tier_version",
        resource_id=tier_version_id,
        request_payload={"tier_id": tier_id},
        response_payload=response,
        before=before,
        after=response,
    )
    return response
