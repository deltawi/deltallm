from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from src.db.callable_target_policies import CallableTargetScopePolicyRecord, CallableTargetScopePolicyRepository
from src.db.callable_targets import CallableTargetBindingRecord, CallableTargetBindingRepository
from src.services.asset_binding_mirror import (
    callable_catalog,
    callable_target_binding_repository,
    delete_route_group_binding_mirror,
    list_all_callable_target_bindings,
    list_all_route_group_bindings,
    mirror_callable_target_binding_to_route_group,
    reload_callable_target_grants,
)
from src.services.asset_visibility_preview import build_asset_visibility_preview
from src.services.callable_targets import CallableTarget

_SCOPES_WITH_POLICY = {"team", "api_key", "user"}
_ALLOWED_SCOPE_TYPES = {"organization", "team", "api_key", "user"}


def _policy_repository(request: Request) -> CallableTargetScopePolicyRepository | None:
    repository = getattr(request.app.state, "callable_target_scope_policy_repository", None)
    if repository is not None and callable(getattr(repository, "list_policies", None)):
        return repository
    return None


def _binding_repository_or_503(request: Request) -> CallableTargetBindingRepository:
    repository = callable_target_binding_repository(request)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Callable target binding repository unavailable",
        )
    return repository


def _policy_repository_or_503(request: Request) -> CallableTargetScopePolicyRepository:
    repository = _policy_repository(request)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Callable target scope policy repository unavailable",
        )
    return repository


def _normalize_scope_type(scope_type: str) -> str:
    normalized = str(scope_type or "").strip().lower()
    if normalized not in _ALLOWED_SCOPE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_SCOPE_TYPES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"scope_type must be one of: {allowed}",
        )
    return normalized


def _normalize_mode(scope_type: str, mode: str | None) -> str:
    normalized_scope_type = _normalize_scope_type(scope_type)
    normalized_mode = str(mode or "").strip().lower()
    if normalized_scope_type == "organization":
        if normalized_mode not in {"", "grant"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="organization asset access mode must be 'grant'",
            )
        return "grant"
    if normalized_scope_type in _SCOPES_WITH_POLICY:
        if normalized_mode not in {"inherit", "restrict"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mode must be one of: inherit, restrict",
            )
        return normalized_mode
    if normalized_mode not in {"", "inherit", "restrict"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be one of: inherit, restrict",
        )
    return normalized_mode or "inherit"


def _normalize_selected_callable_keys(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="selected_callable_keys must be an array",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        callable_key = str(item or "").strip()
        if not callable_key or callable_key in seen:
            continue
        normalized.append(callable_key)
        seen.add(callable_key)
    return normalized


async def build_scope_asset_access(
    request: Request,
    *,
    scope_type: str,
    scope_id: str,
    organization_id: str,
    team_id: str | None = None,
    api_key_id: str | None = None,
    user_id: str | None = None,
    include_targets: bool = True,
) -> dict[str, Any]:
    normalized_scope_type = _normalize_scope_type(scope_type)
    mode = await _resolved_mode(
        request,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
    )
    direct_bindings = await _list_scope_bindings(
        request,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
    )
    direct_selected = {
        binding.callable_key
        for binding in direct_bindings
        if binding.enabled
    }
    selectable_keys = await _selectable_callable_keys(
        request,
        scope_type=normalized_scope_type,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=api_key_id,
        user_id=user_id,
    )
    effective_keys = await _effective_callable_keys(
        request,
        scope_type=normalized_scope_type,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=api_key_id,
        user_id=user_id,
    )
    catalog = callable_catalog(request)

    selectable_targets: list[dict[str, Any]] = []
    effective_targets: list[dict[str, Any]] = []
    if include_targets:
        visible_keys = sorted(selectable_keys | direct_selected)
        selectable_targets = [
            _target_payload(
                catalog.get(callable_key) or CallableTarget(key=callable_key, target_type="model"),
                selectable=callable_key in selectable_keys,
                selected=callable_key in direct_selected,
                effective_visible=callable_key in effective_keys,
                inherited_only=callable_key in effective_keys and callable_key not in direct_selected,
            )
            for callable_key in visible_keys
        ]
        effective_targets = [
            _target_payload(
                catalog.get(callable_key) or CallableTarget(key=callable_key, target_type="model"),
                selectable=callable_key in selectable_keys,
                selected=callable_key in direct_selected,
                effective_visible=True,
                inherited_only=callable_key not in direct_selected,
            )
            for callable_key in sorted(effective_keys)
        ]

    return {
        "scope_type": normalized_scope_type,
        "scope_id": scope_id,
        "organization_id": organization_id,
        "team_id": team_id,
        "api_key_id": api_key_id,
        "user_id": user_id,
        "mode": mode,
        "selected_callable_keys": sorted(direct_selected),
        "selectable_targets": selectable_targets,
        "effective_targets": effective_targets,
        "summary": {
            "selected_total": len(direct_selected),
            "selectable_total": len(selectable_keys),
            "effective_total": len(effective_keys),
        },
    }


async def apply_scope_asset_access(
    request: Request,
    *,
    scope_type: str,
    scope_id: str,
    organization_id: str,
    mode: str | None,
    selected_callable_keys: list[str],
    team_id: str | None = None,
    api_key_id: str | None = None,
    user_id: str | None = None,
    select_all_selectable: bool = False,
) -> dict[str, Any]:
    await sync_scope_asset_access_state(
        request,
        scope_type=scope_type,
        scope_id=scope_id,
        organization_id=organization_id,
        mode=mode,
        selected_callable_keys=selected_callable_keys,
        team_id=team_id,
        api_key_id=api_key_id,
        user_id=user_id,
        select_all_selectable=select_all_selectable,
    )

    return await build_scope_asset_access(
        request,
        scope_type=_normalize_scope_type(scope_type),
        scope_id=scope_id,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=api_key_id,
        user_id=user_id,
    )


async def sync_scope_asset_access_state(
    request: Request,
    *,
    scope_type: str,
    scope_id: str,
    organization_id: str,
    mode: str | None,
    selected_callable_keys: list[str],
    team_id: str | None = None,
    api_key_id: str | None = None,
    user_id: str | None = None,
    select_all_selectable: bool = False,
    binding_repository: CallableTargetBindingRepository | None = None,
    policy_repository: CallableTargetScopePolicyRepository | None = None,
    route_group_repository: Any | None = None,
    reload_after_write: bool = True,
) -> None:
    normalized_scope_type = _normalize_scope_type(scope_type)
    normalized_mode = _normalize_mode(normalized_scope_type, mode)
    normalized_selected = _normalize_selected_callable_keys(selected_callable_keys)
    catalog = callable_catalog(request)

    selectable_keys = await _selectable_callable_keys(
        request,
        scope_type=normalized_scope_type,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=api_key_id,
        user_id=user_id,
    )
    if select_all_selectable:
        if normalized_mode == "inherit":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="select_all_selectable cannot be used when mode is inherit",
            )
        normalized_selected = sorted(selectable_keys)

    missing = sorted(key for key in normalized_selected if key not in catalog)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown callable targets: {', '.join(missing)}",
        )
    outside_parent = sorted(key for key in normalized_selected if key not in selectable_keys)
    if outside_parent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Selected callable targets are outside the parent scope: {', '.join(outside_parent)}",
        )

    if normalized_mode == "inherit" and normalized_selected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="selected_callable_keys must be empty when mode is inherit",
        )

    repository = binding_repository or _binding_repository_or_503(request)
    await _sync_scope_bindings(
        request,
        repository=repository,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
        selected_callable_keys=normalized_selected if normalized_mode != "inherit" else [],
        catalog=catalog,
        route_group_repository=route_group_repository,
    )
    await _sync_scope_policy(
        request,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
        mode=normalized_mode,
        policy_repository=policy_repository,
    )
    if reload_after_write:
        await reload_callable_target_grants(request)


async def _resolved_mode(request: Request, *, scope_type: str, scope_id: str) -> str:
    if scope_type == "organization":
        return "grant"
    if scope_type in _SCOPES_WITH_POLICY:
        record = await _get_scope_policy_record(request, scope_type=scope_type, scope_id=scope_id)
        if record is not None:
            return record.mode
        if scope_type == "user":
            bindings = await _list_scope_bindings(request, scope_type=scope_type, scope_id=scope_id)
            return "restrict" if any(binding.enabled for binding in bindings) else "inherit"
        return "inherit"
    bindings = await _list_scope_bindings(request, scope_type=scope_type, scope_id=scope_id)
    return "restrict" if any(binding.enabled for binding in bindings) else "inherit"


async def _list_scope_bindings(
    request: Request,
    *,
    scope_type: str,
    scope_id: str,
) -> list[CallableTargetBindingRecord]:
    repository = _binding_repository_or_503(request)
    return await list_all_callable_target_bindings(
        repository,
        scope_type=scope_type,
        scope_id=scope_id,
    )


async def _get_scope_policy_record(
    request: Request,
    *,
    scope_type: str,
    scope_id: str,
) -> CallableTargetScopePolicyRecord | None:
    if scope_type not in _SCOPES_WITH_POLICY:
        return None
    repository = _policy_repository_or_503(request)
    policies, _ = await repository.list_policies(
        scope_type=scope_type,
        scope_id=scope_id,
        limit=1,
        offset=0,
    )
    return policies[0] if policies else None


async def _selectable_callable_keys(
    request: Request,
    *,
    scope_type: str,
    organization_id: str,
    team_id: str | None,
    api_key_id: str | None,
    user_id: str | None,
) -> set[str]:
    catalog_keys = set(callable_catalog(request).keys())
    if scope_type == "organization":
        return catalog_keys
    if scope_type == "team":
        return await _preview_effective_callable_keys(
            request,
            organization_id=organization_id,
        )
    if scope_type == "api_key":
        return await _preview_effective_callable_keys(
            request,
            organization_id=organization_id,
            team_id=team_id,
        )
    if scope_type == "user":
        return await _preview_effective_callable_keys(
            request,
            organization_id=organization_id,
            team_id=team_id,
            api_key_id=api_key_id,
        )
    return set()


async def _effective_callable_keys(
    request: Request,
    *,
    scope_type: str,
    organization_id: str,
    team_id: str | None,
    api_key_id: str | None,
    user_id: str | None,
) -> set[str]:
    if scope_type == "organization":
        return await _preview_effective_callable_keys(
            request,
            organization_id=organization_id,
        )
    if scope_type == "team":
        return await _preview_effective_callable_keys(
            request,
            organization_id=organization_id,
            team_id=team_id,
        )
    if scope_type == "api_key":
        return await _preview_effective_callable_keys(
            request,
            organization_id=organization_id,
            team_id=team_id,
            api_key_id=api_key_id,
        )
    return await _preview_effective_callable_keys(
        request,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=api_key_id,
        user_id=user_id,
    )


async def _preview_effective_callable_keys(
    request: Request,
    *,
    organization_id: str,
    team_id: str | None = None,
    api_key_id: str | None = None,
    user_id: str | None = None,
) -> set[str]:
    preview = await build_asset_visibility_preview(
        request,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=api_key_id,
        user_id=user_id,
    )
    return {
        str(item.get("callable_key") or "")
        for item in preview.get("callable_targets", {}).get("items", [])
        if str(item.get("callable_key") or "").strip() and bool(item.get("effective_visible", False))
    }


async def _sync_scope_bindings(
    request: Request,
    *,
    repository: CallableTargetBindingRepository,
    scope_type: str,
    scope_id: str,
    selected_callable_keys: list[str],
    catalog: dict[str, CallableTarget],
    route_group_repository: Any | None = None,
) -> None:
    selected_set = set(selected_callable_keys)
    existing = await list_all_callable_target_bindings(
        repository,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    existing_by_key = {binding.callable_key: binding for binding in existing}

    for callable_key in selected_callable_keys:
        existing_binding = existing_by_key.get(callable_key)
        metadata = dict(existing_binding.metadata) if existing_binding and existing_binding.metadata is not None else None
        await repository.upsert_binding(
            callable_key=callable_key,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=True,
            metadata=metadata,
        )
        target = catalog.get(callable_key)
        if target is not None and target.target_type == "route_group":
            if route_group_repository is not None:
                if await route_group_repository.get_group(callable_key) is not None:
                    await route_group_repository.upsert_binding(
                        callable_key,
                        scope_type=scope_type,
                        scope_id=scope_id,
                        enabled=True,
                        metadata=metadata,
                    )
            else:
                await mirror_callable_target_binding_to_route_group(
                    request,
                    callable_key=callable_key,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    enabled=True,
                    metadata=metadata,
                )

    for callable_key, binding in existing_by_key.items():
        if callable_key in selected_set:
            continue
        await repository.delete_binding(binding.callable_target_binding_id)
        target = catalog.get(callable_key)
        if target is not None and target.target_type == "route_group":
            if route_group_repository is not None:
                bindings = await list_all_route_group_bindings(
                    route_group_repository,
                    group_key=callable_key,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
                for route_group_binding in bindings:
                    await route_group_repository.delete_binding(route_group_binding.route_group_binding_id)
            else:
                await delete_route_group_binding_mirror(
                    request,
                    group_key=callable_key,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )


async def _sync_scope_policy(
    request: Request,
    *,
    scope_type: str,
    scope_id: str,
    mode: str,
    policy_repository: CallableTargetScopePolicyRepository | None = None,
) -> None:
    if scope_type not in _SCOPES_WITH_POLICY:
        return
    repository = policy_repository or _policy_repository_or_503(request)
    if mode == "restrict":
        await repository.upsert_policy(
            scope_type=scope_type,
            scope_id=scope_id,
            mode="restrict",
            metadata=None,
        )
        return

    policies, _ = await repository.list_policies(
        scope_type=scope_type,
        scope_id=scope_id,
        limit=50,
        offset=0,
    )
    for policy in policies:
        await repository.delete_policy(policy.callable_target_scope_policy_id)


def _target_payload(
    target: CallableTarget,
    *,
    selectable: bool,
    selected: bool,
    effective_visible: bool,
    inherited_only: bool,
) -> dict[str, Any]:
    return {
        "callable_key": target.key,
        "target_type": target.target_type,
        "selectable": selectable,
        "selected": selected,
        "effective_visible": effective_visible,
        "inherited_only": inherited_only,
    }
