from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit
from src.api.admin.endpoints.tier_schemas import (
    TierActivationRequest,
    TierCapacityPoolCreateRequest,
    TierCapacityPoolPatchRequest,
    TierCapacityPoolReplaceRequest,
    TierConfigurationMutationRequest,
    TierCreateRequest,
    TierModelPolicyCreateRequest,
    TierModelPolicyPatchRequest,
    TierModelPolicyReplaceRequest,
    TierPatchRequest,
    TierVersionCreateRequest,
)
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.services.tier_policy_invalidation import reload_tier_policy
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


def _configuration_mutation_metadata(result: Any) -> dict[str, Any]:
    updated_at = getattr(result, "version_updated_at", None)
    return {
        "configuration_revision": int(result.configuration_revision),
        "version_updated_at": updated_at.isoformat() if updated_at is not None else None,
    }


def _actor_account_id(request: Request) -> str | None:
    context = get_platform_auth_context(request)
    return str(context.account_id) if context is not None and context.account_id else None


def _actor_provenance(request: Request) -> tuple[str | None, str]:
    account_id = _actor_account_id(request)
    return (account_id, "account") if account_id else (None, "master_key")


def _bootstrap_principal_scope(request: Request) -> str:
    account_id = _actor_account_id(request)
    return f"account:{account_id}" if account_id else "master_key"


async def _reload_tier_policy_for_audit(request: Request) -> dict[str, Any]:
    return (await reload_tier_policy(request)).to_dict()


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


@router.post("/ui/api/tiers/bootstrap", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def bootstrap_tier(
    request: Request,
    payload: TierCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    request_payload = _payload(payload)
    account_id, actor_kind = _actor_provenance(request)
    try:
        result = await _tier_service(request).create_tier_with_initial_draft(
            request_payload,
            principal_scope=_bootstrap_principal_scope(request),
            idempotency_key=idempotency_key or "",
            created_by_account_id=account_id,
            created_by_kind=actor_kind,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = {
        "tier": serialize_tier(result.tier),
        "initial_version": serialize_tier_version(result.initial_version),
        "idempotency_resolution": result.idempotency_resolution,
    }
    if result.idempotency_resolution == "created":
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_TIER_CREATE,
            resource_type="tier",
            resource_id=result.tier.tier_id,
            request_payload=request_payload,
            response_payload=response["tier"],
            before=None,
            after=response["tier"],
        )
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_TIER_VERSION_CREATE,
            resource_type="tier_version",
            resource_id=result.initial_version.tier_version_id,
            request_payload={"tier_id": result.tier.tier_id, "bootstrap": True},
            response_payload=response["initial_version"],
            before=None,
            after=response["initial_version"],
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
    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_UPDATE,
        resource_type="tier",
        resource_id=tier_id,
        request_payload=request_payload,
        response_payload={**response, "tier_policy_invalidation": tier_policy_invalidation},
        before=before,
        after=response,
        metadata={"tier_policy_invalidation": tier_policy_invalidation},
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

    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_DELETE,
        resource_type="tier",
        resource_id=tier_id,
        response_payload={**response, "tier_policy_invalidation": tier_policy_invalidation},
        before=before,
        after=response,
        metadata={"tier_policy_invalidation": tier_policy_invalidation},
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
    account_id, actor_kind = _actor_provenance(request)
    try:
        created = await _tier_service(request).create_tier_version(
            tier_id,
            request_payload,
            created_by_account_id=account_id,
            created_by_kind=actor_kind,
        )
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


@router.get("/ui/api/tiers/{tier_id}/versions", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def list_tier_versions(
    request: Request,
    tier_id: str,
    status_filter: list[str] = Query(default=[], alias="status"),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return await _tier_service(request).list_tier_versions_page(
            tier_id,
            statuses=tuple(status_filter),
            limit=limit,
            offset=offset,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc


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


@router.post(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/clone",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def clone_tier_version(
    request: Request,
    tier_id: str,
    tier_version_id: str,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    account_id, actor_kind = _actor_provenance(request)
    try:
        before = serialize_tier_version(
            await service.require_version_for_tier(tier_id, tier_version_id)
        )
        cloned = await service.clone_tier_version(
            tier_id,
            tier_version_id,
            created_by_account_id=account_id,
            created_by_kind=actor_kind,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = serialize_tier_version(cloned)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_VERSION_CLONE,
        resource_type="tier_version",
        resource_id=cloned.tier_version_id,
        request_payload={
            "tier_id": tier_id,
            "source_tier_version_id": tier_version_id,
        },
        response_payload=response,
        before=before,
        after=response,
    )
    return response


@router.get(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/model-policies",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def list_tier_model_policies(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    search: str | None = Query(default=None, max_length=200),
    enabled: bool | None = Query(default=None),
    access_mode: str | None = Query(default=None, max_length=40),
    capacity_pool_key: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="priority"),
    order: str = Query(default="desc"),
    limit: int = Query(default=10, ge=10, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return await _tier_service(request).list_model_policies_page(
            tier_id,
            tier_version_id,
            search=search,
            enabled=enabled,
            access_mode=access_mode,
            capacity_pool_key=capacity_pool_key,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/model-policies",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def create_tier_model_policy(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    payload: TierModelPolicyCreateRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    request_payload = _payload(payload)
    try:
        result = await _tier_service(request).create_model_policy(
            tier_id,
            tier_version_id,
            request_payload,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc
    if result.policy is None:
        raise HTTPException(status_code=500, detail="Model policy mutation returned no row")
    response = {
        "data": serialize_model_policy(result.policy),
        **_configuration_mutation_metadata(result),
    }
    invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_MODEL_POLICY_CREATE,
        resource_type="tier_model_policy",
        resource_id=result.policy.tier_model_policy_id,
        request_payload={"tier_id": tier_id, "tier_version_id": tier_version_id},
        response_payload={**response, "tier_policy_invalidation": invalidation},
        before=None,
        after=response["data"],
        metadata={"tier_policy_invalidation": invalidation},
    )
    return response


@router.patch(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/model-policies/{policy_id}",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def update_tier_model_policy(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    policy_id: str,
    payload: TierModelPolicyPatchRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    request_payload = _payload(payload)
    before_record = await service.repository.get_model_policy_for_version(
        tier_id=tier_id,
        tier_version_id=tier_version_id,
        tier_model_policy_id=policy_id,
    )
    try:
        result = await service.update_model_policy(
            tier_id,
            tier_version_id,
            policy_id,
            request_payload,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc
    if result.policy is None:
        raise HTTPException(status_code=500, detail="Model policy mutation returned no row")
    response = {
        "data": serialize_model_policy(result.policy),
        **_configuration_mutation_metadata(result),
    }
    invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_MODEL_POLICY_UPDATE,
        resource_type="tier_model_policy",
        resource_id=policy_id,
        request_payload={
            "tier_id": tier_id,
            "tier_version_id": tier_version_id,
            **request_payload,
        },
        response_payload={**response, "tier_policy_invalidation": invalidation},
        before=serialize_model_policy(before_record) if before_record else None,
        after=response["data"],
        metadata={"tier_policy_invalidation": invalidation},
    )
    return response


@router.delete(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/model-policies/{policy_id}",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def delete_tier_model_policy(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    policy_id: str,
    payload: TierConfigurationMutationRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    before_record = await service.repository.get_model_policy_for_version(
        tier_id=tier_id,
        tier_version_id=tier_version_id,
        tier_model_policy_id=policy_id,
    )
    try:
        result = await service.delete_model_policy(
            tier_id,
            tier_version_id,
            policy_id,
            expected_revision=payload.expected_revision,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc
    response = {
        "deleted": True,
        "tier_model_policy_id": policy_id,
        **_configuration_mutation_metadata(result),
    }
    invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_MODEL_POLICY_DELETE,
        resource_type="tier_model_policy",
        resource_id=policy_id,
        request_payload={"tier_id": tier_id, "tier_version_id": tier_version_id},
        response_payload={**response, "tier_policy_invalidation": invalidation},
        before=serialize_model_policy(before_record) if before_record else None,
        after=response,
        metadata={"tier_policy_invalidation": invalidation},
    )
    return response


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
    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
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
        response_payload={
            "policy_count": len(response["data"]),
            "tier_policy_invalidation": tier_policy_invalidation,
        },
        before={"policy_count": len(before_detail["model_policies"]) if before_detail else 0},
        after={"policy_count": len(response["data"])},
        metadata={"tier_policy_invalidation": tier_policy_invalidation},
    )
    return response


@router.get(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/capacity-pools",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def list_tier_capacity_pools(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    search: str | None = Query(default=None, max_length=200),
    strategy: str | None = Query(default=None, max_length=40),
    sort: str = Query(default="pool_key"),
    order: str = Query(default="asc"),
    limit: int = Query(default=10, ge=10, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return await _tier_service(request).list_capacity_pools_page(
            tier_id,
            tier_version_id,
            search=search,
            strategy=strategy,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/capacity-pools",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def create_tier_capacity_pool(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    payload: TierCapacityPoolCreateRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    request_payload = _payload(payload)
    try:
        result = await _tier_service(request).create_capacity_pool(
            tier_id,
            tier_version_id,
            request_payload,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc
    if result.pool is None:
        raise HTTPException(status_code=500, detail="Capacity pool mutation returned no row")
    response = {
        "data": serialize_capacity_pool(result.pool),
        **_configuration_mutation_metadata(result),
    }
    invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_CAPACITY_POOL_CREATE,
        resource_type="tier_capacity_pool",
        resource_id=result.pool.tier_capacity_pool_id,
        request_payload={"tier_id": tier_id, "tier_version_id": tier_version_id},
        response_payload={**response, "tier_policy_invalidation": invalidation},
        before=None,
        after=response["data"],
        metadata={"tier_policy_invalidation": invalidation},
    )
    return response


@router.patch(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/capacity-pools/{pool_id}",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def update_tier_capacity_pool(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    pool_id: str,
    payload: TierCapacityPoolPatchRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    request_payload = _payload(payload)
    before_record = await service.repository.get_capacity_pool_for_version(
        tier_id=tier_id,
        tier_version_id=tier_version_id,
        tier_capacity_pool_id=pool_id,
    )
    try:
        result = await service.update_capacity_pool(
            tier_id,
            tier_version_id,
            pool_id,
            request_payload,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc
    if result.pool is None:
        raise HTTPException(status_code=500, detail="Capacity pool mutation returned no row")
    response = {
        "data": serialize_capacity_pool(result.pool),
        **_configuration_mutation_metadata(result),
    }
    invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_CAPACITY_POOL_UPDATE,
        resource_type="tier_capacity_pool",
        resource_id=pool_id,
        request_payload={
            "tier_id": tier_id,
            "tier_version_id": tier_version_id,
            **request_payload,
        },
        response_payload={**response, "tier_policy_invalidation": invalidation},
        before=serialize_capacity_pool(before_record) if before_record else None,
        after=response["data"],
        metadata={"tier_policy_invalidation": invalidation},
    )
    return response


@router.delete(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/capacity-pools/{pool_id}",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def delete_tier_capacity_pool(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    pool_id: str,
    payload: TierConfigurationMutationRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    before_record = await service.repository.get_capacity_pool_for_version(
        tier_id=tier_id,
        tier_version_id=tier_version_id,
        tier_capacity_pool_id=pool_id,
    )
    try:
        result = await service.delete_capacity_pool(
            tier_id,
            tier_version_id,
            pool_id,
            expected_revision=payload.expected_revision,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc
    response = {
        "deleted": True,
        "tier_capacity_pool_id": pool_id,
        **_configuration_mutation_metadata(result),
    }
    invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_CAPACITY_POOL_DELETE,
        resource_type="tier_capacity_pool",
        resource_id=pool_id,
        request_payload={"tier_id": tier_id, "tier_version_id": tier_version_id},
        response_payload={**response, "tier_policy_invalidation": invalidation},
        before=serialize_capacity_pool(before_record) if before_record else None,
        after=response,
        metadata={"tier_policy_invalidation": invalidation},
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
    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_CAPACITY_POOLS_REPLACE,
        resource_type="tier_capacity_pool_set",
        resource_id=tier_version_id,
        request_payload={"tier_id": tier_id, "pool_count": len(pools_payload)},
        response_payload={
            "pool_count": len(response["data"]),
            "tier_policy_invalidation": tier_policy_invalidation,
        },
        before={"pool_count": len(before_detail["capacity_pools"]) if before_detail else 0},
        after={"pool_count": len(response["data"])},
        metadata={"tier_policy_invalidation": tier_policy_invalidation},
    )
    return response


@router.get(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/activation-preview",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def get_tier_activation_preview(
    request: Request,
    tier_id: str,
    tier_version_id: str,
) -> dict[str, Any]:
    try:
        return await _tier_service(request).get_activation_preview(
            tier_id,
            tier_version_id,
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/activate",
    dependencies=_PLATFORM_ADMIN_DEPENDENCY,
)
async def activate_tier_version(
    request: Request,
    tier_id: str,
    tier_version_id: str,
    payload: TierActivationRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _tier_service(request)
    request_payload = _payload(payload)
    try:
        before = serialize_tier_version(
            await service.require_version_for_tier(tier_id, tier_version_id)
        )
        activated = await service.activate_tier_version(
            tier_id,
            tier_version_id,
            expected_revision=payload.expected_revision,
            expected_active_version_id=payload.expected_active_version_id,
            published_by_account_id=_actor_account_id(request),
        )
    except TierAdminError as exc:
        raise _http_error(exc) from exc

    response = serialize_tier_version(activated)
    invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_VERSION_ACTIVATE,
        resource_type="tier_version",
        resource_id=tier_version_id,
        request_payload={"tier_id": tier_id, **request_payload},
        response_payload={**response, "tier_policy_invalidation": invalidation},
        before=before,
        after=response,
        metadata={"tier_policy_invalidation": invalidation},
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
    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_VERSION_PUBLISH,
        resource_type="tier_version",
        resource_id=tier_version_id,
        request_payload={"tier_id": tier_id},
        response_payload={**response, "tier_policy_invalidation": tier_policy_invalidation},
        before=before,
        after=response,
        metadata={"tier_policy_invalidation": tier_policy_invalidation},
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
    tier_policy_invalidation = await _reload_tier_policy_for_audit(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_VERSION_ARCHIVE,
        resource_type="tier_version",
        resource_id=tier_version_id,
        request_payload={"tier_id": tier_id},
        response_payload={**response, "tier_policy_invalidation": tier_policy_invalidation},
        before=before,
        after=response,
        metadata={"tier_policy_invalidation": tier_policy_invalidation},
    )
    return response
    TierModelPolicyCreateRequest,
    TierModelPolicyPatchRequest,
