from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.services.asset_binding_mirror import (
    callable_catalog,
    callable_target_access_group_binding_repository,
    callable_target_binding_repository,
    delete_route_group_binding_mirror,
    mirror_callable_target_binding_to_route_group,
    reload_callable_target_grants,
    route_group_repository,
)
from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit, resolve_runtime_scope_target, to_json_value
from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.db.callable_target_access_groups import CallableTargetAccessGroupBindingRepository
from src.db.callable_targets import CallableTargetBindingRepository
from src.db.callable_target_policies import CallableTargetScopePolicyRepository
from src.governance.access_groups import (
    InvalidAccessGroupError,
    build_callable_keys_by_access_group,
    normalize_access_group_key,
)
from src.middleware.admin import require_admin_permission
from src.services.asset_scopes import strict_normalize_scope_type
from src.services.callable_target_migration import (
    ORGANIZATION_ROLLOUT_STATES,
    ROLLOUT_STATE_ALIASES,
    apply_callable_target_migration_backfill,
    build_callable_target_migration_report,
)
from src.services.organization_callable_target_sync import maybe_disable_organization_auto_follow_for_scope_mutation
from src.services.callable_targets import CallableTarget

router = APIRouter(tags=["Admin Callable Targets"])

_ALLOWED_SCOPE_TYPES = {"api_key", "key", "team", "organization", "org", "user"}
_POLICY_SCOPE_TYPES = {"api_key", "key", "team", "user"}
_ALLOWED_SCOPE_POLICY_MODES = {"inherit", "restrict"}


def _repository_or_503(request: Request) -> CallableTargetBindingRepository:
    repository = callable_target_binding_repository(request)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Callable target binding repository unavailable")
    return repository


def _access_group_repository_or_503(request: Request) -> CallableTargetAccessGroupBindingRepository:
    repository = callable_target_access_group_binding_repository(request)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Callable target access group repository unavailable",
        )
    return repository


def _policy_repository_or_503(request: Request) -> CallableTargetScopePolicyRepository:
    repository = getattr(request.app.state, "callable_target_scope_policy_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Callable target scope policy repository unavailable")
    return repository


def _validate_scope_type(value: Any) -> str:
    scope_type = str(value or "").strip().lower()
    if scope_type not in _ALLOWED_SCOPE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_SCOPE_TYPES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"scope_type must be one of: {allowed}")
    try:
        normalized = strict_normalize_scope_type(scope_type, allowed={"api_key", "team", "organization", "user"})
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type must be one of: api_key, team, organization, user") from None
    if normalized == "group":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type must be one of: api_key, team, organization, user")
    return normalized


def _validate_policy_scope_type(value: Any) -> str:
    scope_type = str(value or "").strip().lower()
    if scope_type not in _POLICY_SCOPE_TYPES:
        allowed = ", ".join(sorted(_POLICY_SCOPE_TYPES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"scope_type must be one of: {allowed}")
    try:
        normalized = strict_normalize_scope_type(scope_type, allowed={"api_key", "team", "user"})
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_type must be one of: api_key, team, user")
    return normalized


def _validate_scope_id(value: Any, *, field_name: str = "scope_id") -> str:
    scope_id = str(value or "").strip()
    if not scope_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} is required")
    return scope_id


def _validate_access_group_key(value: Any) -> str:
    try:
        group_key = normalize_access_group_key(value, strict=True)
    except InvalidAccessGroupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if group_key is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="group_key is required")
    return group_key


def _validated_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")
    return dict(value)


def _validate_scope_policy_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in _ALLOWED_SCOPE_POLICY_MODES:
        allowed = ", ".join(sorted(_ALLOWED_SCOPE_POLICY_MODES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"mode must be one of: {allowed}")
    return mode


def _validate_rollout_states(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    normalized = {
        ROLLOUT_STATE_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())
        for value in values
        if str(value or "").strip()
    }
    invalid = sorted(value for value in normalized if value not in ORGANIZATION_ROLLOUT_STATES)
    if invalid:
        allowed = ", ".join(sorted(ORGANIZATION_ROLLOUT_STATES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"rollout_state must be one of: {allowed}",
        )
    return normalized


def _validate_callable_key(request: Request, callable_key: Any) -> CallableTarget:
    normalized = str(callable_key or "").strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="callable_key is required")
    target = callable_catalog(request).get(normalized)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target not found")
    return target


def _binding_payload(binding: Any) -> dict[str, Any]:
    return to_json_value(asdict(binding))


def _access_group_binding_payload(binding: Any) -> dict[str, Any]:
    return to_json_value(asdict(binding))


def _scope_policy_payload(policy: Any) -> dict[str, Any]:
    return to_json_value(asdict(policy))


def _callable_target_payload(target: CallableTarget, *, binding_count: int = 0) -> dict[str, Any]:
    return {"callable_key": target.key, "target_type": target.target_type, "binding_count": binding_count}


def _access_group_payload(
    *,
    group_key: str,
    member_keys: frozenset[str],
    catalog: dict[str, CallableTarget],
    binding_count: int,
    include_members: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "group_key": group_key,
        "member_count": len(member_keys),
        "binding_count": binding_count,
    }
    if include_members:
        payload["members"] = [
            {
                "callable_key": callable_key,
                "target_type": (catalog.get(callable_key) or CallableTarget(key=callable_key, target_type="model")).target_type,
            }
            for callable_key in sorted(member_keys)
        ]
    return payload


async def _list_access_group_binding_counts(
    repository: CallableTargetAccessGroupBindingRepository,
    *,
    search: str | None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    offset = 0
    page_size = 500
    while True:
        page, total = await repository.list_group_binding_counts(
            search=search,
            limit=page_size,
            offset=offset,
        )
        for item in page:
            group_key = str(item.group_key or "").strip()
            if group_key:
                counts[group_key] = int(item.binding_count or 0)
        offset += len(page)
        if not page or offset >= total:
            break
    return counts


@router.get("/ui/api/callable-targets", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_callable_targets(
    request: Request,
    search: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    catalog = list(callable_catalog(request).values())

    if search:
        query = search.strip().lower()
        catalog = [item for item in catalog if query in item.key.lower()]
    if target_type:
        normalized_type = str(target_type).strip().lower()
        if normalized_type not in {"model", "route_group"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target_type must be one of: model, route_group")
        catalog = [item for item in catalog if item.target_type == normalized_type]

    bindings, _ = await repository.list_bindings(limit=1000, offset=0)
    binding_counts: dict[str, int] = {}
    for binding in bindings:
        binding_counts[binding.callable_key] = binding_counts.get(binding.callable_key, 0) + 1

    total = len(catalog)
    page = catalog[offset : offset + limit]
    return {
        "data": [_callable_target_payload(item, binding_count=binding_counts.get(item.key, 0)) for item in page],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/callable-target-access-groups", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_callable_target_access_groups(
    request: Request,
    search: str | None = Query(default=None),
    include_members: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _access_group_repository_or_503(request)
    catalog = callable_catalog(request)
    callable_keys_by_group = build_callable_keys_by_access_group(catalog)
    query = str(search or "").strip().lower() or None
    catalog_group_keys = set(callable_keys_by_group)
    if query:
        catalog_group_keys = {group_key for group_key in catalog_group_keys if query in group_key.lower()}
    binding_counts = await _list_access_group_binding_counts(repository, search=query)
    group_keys = catalog_group_keys | set(binding_counts)

    ordered = sorted(group_keys)
    total = len(ordered)
    page = ordered[offset : offset + limit]
    return {
        "data": [
            _access_group_payload(
                group_key=group_key,
                member_keys=callable_keys_by_group.get(group_key, frozenset()),
                catalog=catalog,
                binding_count=binding_counts.get(group_key, 0),
                include_members=include_members,
            )
            for group_key in page
        ],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/callable-target-access-group-bindings", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_callable_target_access_group_bindings(
    request: Request,
    group_key: str | None = Query(default=None),
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _access_group_repository_or_503(request)
    normalized_group_key = _validate_access_group_key(group_key) if group_key is not None else None
    normalized_scope_type = _validate_scope_type(scope_type) if scope_type is not None else None
    bindings, total = await repository.list_bindings(
        group_key=normalized_group_key,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
        limit=limit,
        offset=offset,
    )
    return {
        "data": [_access_group_binding_payload(binding) for binding in bindings],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/callable-target-access-group-bindings", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def upsert_callable_target_access_group_binding(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _access_group_repository_or_503(request)
    group_key = _validate_access_group_key(payload.get("group_key"))
    scope_type = _validate_scope_type(payload.get("scope_type"))
    scope_id = _validate_scope_id(payload.get("scope_id"))
    await resolve_runtime_scope_target(
        db_or_503(request),
        scope_type=scope_type,
        scope_id=scope_id,
    )

    binding = await repository.upsert_binding(
        group_key=group_key,
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=bool(payload.get("enabled", True)),
        metadata=_validated_metadata(payload.get("metadata")),
    )
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target access group binding not found")

    await maybe_disable_organization_auto_follow_for_scope_mutation(
        db_or_503(request),
        scope_type=binding.scope_type,
        scope_id=binding.scope_id,
    )
    await reload_callable_target_grants(request)
    response = _access_group_binding_payload(binding)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_CALLABLE_TARGET_ACCESS_GROUP_BINDING_UPSERT,
        resource_type="callable_target_access_group_binding",
        resource_id=binding.callable_target_access_group_binding_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.get("/ui/api/callable-targets/{callable_key:path}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def get_callable_target(request: Request, callable_key: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    target = _validate_callable_key(request, callable_key)
    bindings, _ = await repository.list_bindings(callable_key=target.key, limit=200, offset=0)
    return {
        "target": _callable_target_payload(target, binding_count=len(bindings)),
        "bindings": [_binding_payload(binding) for binding in bindings],
    }


@router.get("/ui/api/callable-target-bindings", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_callable_target_bindings(
    request: Request,
    callable_key: str | None = Query(default=None),
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    normalized_scope_type = _validate_scope_type(scope_type) if scope_type is not None else None
    bindings, total = await repository.list_bindings(
        callable_key=callable_key,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
        limit=limit,
        offset=offset,
    )
    return {
        "data": [_binding_payload(binding) for binding in bindings],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/callable-target-bindings", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def upsert_callable_target_binding(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    target = _validate_callable_key(request, payload.get("callable_key"))
    scope_type = _validate_scope_type(payload.get("scope_type"))
    scope_id = _validate_scope_id(payload.get("scope_id"))
    await resolve_runtime_scope_target(
        db_or_503(request),
        scope_type=scope_type,
        scope_id=scope_id,
    )

    binding = await repository.upsert_binding(
        callable_key=target.key,
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=bool(payload.get("enabled", True)),
        metadata=_validated_metadata(payload.get("metadata")),
    )
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target not found")

    if target.target_type == "route_group":
        await mirror_callable_target_binding_to_route_group(
            request,
            callable_key=target.key,
            scope_type=binding.scope_type,
            scope_id=binding.scope_id,
            enabled=binding.enabled,
            metadata=binding.metadata,
        )
    await maybe_disable_organization_auto_follow_for_scope_mutation(
        db_or_503(request),
        scope_type=binding.scope_type,
        scope_id=binding.scope_id,
    )
    await reload_callable_target_grants(request)
    response = _binding_payload(binding)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_CALLABLE_TARGET_BINDING_UPSERT,
        resource_type="callable_target_binding",
        resource_id=binding.callable_target_binding_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.get("/ui/api/callable-target-scope-policies", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_callable_target_scope_policies(
    request: Request,
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _policy_repository_or_503(request)
    normalized_scope_type = _validate_policy_scope_type(scope_type) if scope_type is not None else None
    policies, total = await repository.list_policies(
        scope_type=normalized_scope_type,
        scope_id=scope_id,
        limit=limit,
        offset=offset,
    )
    return {
        "data": [_scope_policy_payload(policy) for policy in policies],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/callable-target-scope-policies", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def upsert_callable_target_scope_policy(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _policy_repository_or_503(request)
    scope_type = _validate_policy_scope_type(payload.get("scope_type"))
    scope_id = _validate_scope_id(payload.get("scope_id"))
    await resolve_runtime_scope_target(
        db_or_503(request),
        scope_type=scope_type,
        scope_id=scope_id,
    )

    policy = await repository.upsert_policy(
        scope_type=scope_type,
        scope_id=scope_id,
        mode=_validate_scope_policy_mode(payload.get("mode")),
        metadata=_validated_metadata(payload.get("metadata")),
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target scope policy not found")

    await reload_callable_target_grants(request)
    response = _scope_policy_payload(policy)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_CALLABLE_TARGET_SCOPE_POLICY_UPSERT,
        resource_type="callable_target_scope_policy",
        resource_id=policy.callable_target_scope_policy_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/callable-target-scope-policies/{policy_id}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def delete_callable_target_scope_policy(request: Request, policy_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _policy_repository_or_503(request)
    policy = await repository.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target scope policy not found")

    deleted = await repository.delete_policy(policy_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target scope policy not found")

    await reload_callable_target_grants(request)
    response = {"deleted": True, "callable_target_scope_policy_id": policy_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_CALLABLE_TARGET_SCOPE_POLICY_DELETE,
        resource_type="callable_target_scope_policy",
        resource_id=policy_id,
        response_payload=response,
    )
    return response


@router.delete(
    "/ui/api/callable-target-access-group-bindings/{binding_id}",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def delete_callable_target_access_group_binding(request: Request, binding_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _access_group_repository_or_503(request)
    binding = await repository.get_binding(binding_id)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target access group binding not found")

    deleted = await repository.delete_binding(binding_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target access group binding not found")

    await maybe_disable_organization_auto_follow_for_scope_mutation(
        db_or_503(request),
        scope_type=binding.scope_type,
        scope_id=binding.scope_id,
    )
    await reload_callable_target_grants(request)
    response = {"deleted": True, "callable_target_access_group_binding_id": binding_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_CALLABLE_TARGET_ACCESS_GROUP_BINDING_DELETE,
        resource_type="callable_target_access_group_binding",
        resource_id=binding_id,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/callable-target-bindings/{binding_id}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def delete_callable_target_binding(request: Request, binding_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    binding = await repository.get_binding(binding_id)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target binding not found")

    deleted = await repository.delete_binding(binding_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Callable target binding not found")

    target = callable_catalog(request).get(binding.callable_key)
    if target is None:
        group_repository = route_group_repository(request)
        if group_repository is not None and await group_repository.get_group(binding.callable_key) is not None:
            target = CallableTarget(key=binding.callable_key, target_type="route_group")
    if target is not None and target.target_type == "route_group":
        await delete_route_group_binding_mirror(
            request,
            group_key=binding.callable_key,
            scope_type=binding.scope_type,
            scope_id=binding.scope_id,
        )
    await maybe_disable_organization_auto_follow_for_scope_mutation(
        db_or_503(request),
        scope_type=binding.scope_type,
        scope_id=binding.scope_id,
    )
    await reload_callable_target_grants(request)
    response = {"deleted": True, "callable_target_binding_id": binding_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_CALLABLE_TARGET_BINDING_DELETE,
        resource_type="callable_target_binding",
        resource_id=binding_id,
        response_payload=response,
    )
    return response


@router.get("/ui/api/callable-target-migration/report", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def get_callable_target_migration_report(
    request: Request,
    organization_id: str | None = Query(default=None),
    rollout_state: list[str] | None = Query(default=None),
) -> dict[str, Any]:
    db = db_or_503(request)
    return await build_callable_target_migration_report(
        db=db,
        callable_catalog=callable_catalog(request),
        binding_repository=_repository_or_503(request),
        policy_repository=_policy_repository_or_503(request),
        route_group_repository=route_group_repository(request),
        organization_id=_validate_scope_id(organization_id, field_name="organization_id") if organization_id is not None else None,
        rollout_states=_validate_rollout_states(rollout_state),
    )


@router.post("/ui/api/callable-target-migration/backfill", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def backfill_callable_target_migration(
    request: Request,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_start = perf_counter()
    normalized_payload = payload or {}
    rollout_states = _validate_rollout_states(normalized_payload.get("rollout_states"))
    organization_id = (
        _validate_scope_id(normalized_payload.get("organization_id"), field_name="organization_id")
        if normalized_payload.get("organization_id") is not None
        else None
    )
    db = db_or_503(request)
    response = await apply_callable_target_migration_backfill(
        db=db,
        callable_catalog=callable_catalog(request),
        route_group_repository=route_group_repository(request),
        binding_repository=_repository_or_503(request),
        policy_repository=_policy_repository_or_503(request),
        organization_id=organization_id,
        rollout_states=rollout_states,
    )
    await reload_callable_target_grants(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_CALLABLE_TARGET_MIGRATION_BACKFILL,
        resource_type="callable_target_migration",
        resource_id=organization_id or "all",
        request_payload=normalized_payload,
        response_payload=response,
    )
    return response
