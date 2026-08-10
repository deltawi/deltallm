from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.admin.endpoints.common import emit_admin_mutation_audit
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.services.tier_capacity_fair_share import (
    build_tier_capacity_dashboard,
    delete_temporary_capacity_boost,
    is_advanced_capacity_pool_strategy,
    upsert_temporary_capacity_boost,
)

router = APIRouter(tags=["Admin Tier Capacity"])
_PLATFORM_ADMIN_DEPENDENCY = [Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TierCapacityBoostRequest(_StrictRequest):
    organization_id: str = Field(min_length=1, max_length=200)
    pool_key: str = Field(min_length=1, max_length=200)
    callable_key: str = Field(min_length=1, max_length=300)
    weight_multiplier: float = Field(default=2.0, ge=1.0, le=100.0)
    ttl_seconds: int = Field(default=3600, ge=1, le=604800)
    reason: str | None = Field(default=None, max_length=500)


def _tier_policy_service_or_503(request: Request) -> Any:
    service = getattr(request.app.state, "tier_policy_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tier policy service unavailable",
        )
    return service


def _redis_or_503(request: Request) -> Any:
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis unavailable",
        )
    return redis_client


def _payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="python", exclude_unset=True)


def _validate_boost_target(
    tier_policy_service: Any,
    *,
    pool_key: str,
    callable_key: str,
    organization_id: str,
    require_active_member: bool = True,
) -> None:
    snapshot_getter = getattr(tier_policy_service, "get_snapshot", None)
    if not callable(snapshot_getter):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tier policy snapshot unavailable",
        )
    snapshot = snapshot_getter()
    pool = getattr(snapshot, "capacity_pool_policy", {}).get((pool_key, callable_key))
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Capacity pool not found in active tier policy snapshot",
        )
    if not is_advanced_capacity_pool_strategy(getattr(pool, "strategy", None)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Capacity pool does not use advanced fair-share",
        )
    members = getattr(snapshot, "capacity_pool_members", {}).get((pool_key, callable_key), ())
    if require_active_member and not any(getattr(member, "organization_id", None) == organization_id for member in members):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization is not an active member of this capacity pool",
        )


@router.get("/ui/api/tier-capacity/dashboard", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def tier_capacity_dashboard(
    request: Request,
    top_org_limit: int = Query(default=10, ge=1, le=50),
    pool_limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    try:
        return await build_tier_capacity_dashboard(
            tier_policy_service=_tier_policy_service_or_503(request),
            redis_client=getattr(request.app.state, "redis", None),
            top_org_limit=top_org_limit,
            pool_limit=pool_limit,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post("/ui/api/tier-capacity/boosts", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def upsert_tier_capacity_boost(
    request: Request,
    payload: TierCapacityBoostRequest,
) -> dict[str, Any]:
    request_start = perf_counter()
    request_payload = _payload(payload)
    _validate_boost_target(
        _tier_policy_service_or_503(request),
        pool_key=payload.pool_key,
        callable_key=payload.callable_key,
        organization_id=payload.organization_id,
    )
    response = await upsert_temporary_capacity_boost(
        redis_client=_redis_or_503(request),
        pool_key=payload.pool_key,
        callable_key=payload.callable_key,
        organization_id=payload.organization_id,
        weight_multiplier=payload.weight_multiplier,
        ttl_seconds=payload.ttl_seconds,
        reason=payload.reason,
    )
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_CAPACITY_BOOST_UPSERT,
        resource_type="tier_capacity_boost",
        resource_id=f"{payload.pool_key}:{payload.callable_key}:{payload.organization_id}",
        request_payload=request_payload,
        response_payload=response,
        before=None,
        after=response,
    )
    return response


@router.delete("/ui/api/tier-capacity/boosts", dependencies=_PLATFORM_ADMIN_DEPENDENCY)
async def delete_tier_capacity_boost(
    request: Request,
    organization_id: str = Query(min_length=1, max_length=200),
    pool_key: str = Query(min_length=1, max_length=200),
    callable_key: str = Query(min_length=1, max_length=300),
) -> dict[str, Any]:
    request_start = perf_counter()
    _validate_boost_target(
        _tier_policy_service_or_503(request),
        pool_key=pool_key,
        callable_key=callable_key,
        organization_id=organization_id,
        require_active_member=False,
    )
    response = await delete_temporary_capacity_boost(
        redis_client=_redis_or_503(request),
        pool_key=pool_key,
        callable_key=callable_key,
        organization_id=organization_id,
    )
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TIER_CAPACITY_BOOST_DELETE,
        resource_type="tier_capacity_boost",
        resource_id=f"{pool_key}:{callable_key}:{organization_id}",
        request_payload={
            "organization_id": organization_id,
            "pool_key": pool_key,
            "callable_key": callable_key,
        },
        response_payload=response,
        before=None,
        after=response,
    )
    return response
