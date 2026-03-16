from __future__ import annotations

from typing import Any

from fastapi import Request

from src.db.callable_target_policies import CallableTargetScopePolicyRepository
from src.db.route_groups import RouteGroupRepository
from src.services.asset_binding_mirror import (
    callable_catalog,
    callable_target_binding_repository,
    list_all_callable_target_bindings,
    list_all_route_group_bindings,
    route_group_repository,
)
from src.services.callable_targets import CallableTarget


def _callable_target_scope_policy_repository(request: Request) -> CallableTargetScopePolicyRepository | None:
    repository = getattr(request.app.state, "callable_target_scope_policy_repository", None)
    if repository is not None and callable(getattr(repository, "list_policies", None)):
        return repository
    return None

async def list_scope_route_group_bindings(request: Request, *, scope_type: str, scope_id: str) -> list[dict[str, Any]]:
    repository = route_group_repository(request)
    if repository is None:
        return []
    bindings = await list_all_route_group_bindings(
        repository,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    return [
        {
            "group_key": binding.group_key,
            "scope_type": binding.scope_type,
            "scope_id": binding.scope_id,
            "enabled": binding.enabled,
            "metadata": binding.metadata,
        }
        for binding in bindings
    ]


async def list_scope_callable_target_bindings(request: Request, *, scope_type: str, scope_id: str) -> list[dict[str, Any]]:
    repository = callable_target_binding_repository(request)
    if repository is None:
        return []
    bindings = await list_all_callable_target_bindings(
        repository,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    return [
        {
            "callable_key": binding.callable_key,
            "scope_type": binding.scope_type,
            "scope_id": binding.scope_id,
            "enabled": binding.enabled,
            "metadata": binding.metadata,
        }
        for binding in bindings
    ]


async def get_scope_callable_target_policy_mode(
    request: Request,
    *,
    scope_type: str,
    scope_id: str | None,
) -> str | None:
    if not scope_id:
        return None
    repository = _callable_target_scope_policy_repository(request)
    if repository is None:
        return None
    policies, _ = await repository.list_policies(scope_type=scope_type, scope_id=scope_id, limit=1, offset=0)
    if not policies:
        return None
    return str(policies[0].mode or "inherit")


async def list_owned_route_groups(
    request: Request,
    *,
    owner_scope_type: str,
    owner_scope_id: str | None,
) -> list[dict[str, Any]]:
    repository = route_group_repository(request)
    if repository is None:
        return []
    groups = await _list_all_route_groups(repository)
    return [
        {
            "group_key": group.group_key,
            "owner_scope_type": group.owner_scope_type,
            "owner_scope_id": group.owner_scope_id,
            "enabled": group.enabled,
        }
        for group in groups
        if group.owner_scope_type == owner_scope_type and group.owner_scope_id == owner_scope_id
    ]


async def _list_all_route_groups(repository: RouteGroupRepository | None) -> list[Any]:
    if repository is None:
        return []
    items: list[Any] = []
    offset = 0
    page_size = 500
    while True:
        page, total = await repository.list_groups(limit=page_size, offset=offset)
        items.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    return items


def _target_for_callable_key(
    *,
    callable_key: str,
    callable_catalog_by_key: dict[str, CallableTarget],
    route_group_keys: set[str],
) -> CallableTarget:
    if callable_key in callable_catalog_by_key:
        return callable_catalog_by_key[callable_key]
    if callable_key in route_group_keys:
        return CallableTarget(key=callable_key, target_type="route_group")
    return CallableTarget(key=callable_key, target_type="model")


def _add_route_group_source(
    route_group_items: dict[str, dict[str, Any]],
    *,
    group_key: str,
    source_scope_type: str,
    source_scope_id: str,
    kind: str,
    enabled: bool,
    metadata: dict[str, Any] | None = None,
    owner_scope_type: str | None = None,
    owner_scope_id: str | None = None,
) -> None:
    item = route_group_items.setdefault(
        group_key,
        {
            "group_key": group_key,
            "owner_scope_type": owner_scope_type,
            "owner_scope_id": owner_scope_id,
            "effective_enabled": False,
            "effective_visible": False,
            "visibility_source": "granted",
            "sources": [],
        },
    )
    if owner_scope_type is not None:
        item["owner_scope_type"] = owner_scope_type
    if owner_scope_id is not None:
        item["owner_scope_id"] = owner_scope_id
    item["effective_enabled"] = bool(item["effective_enabled"] or enabled)
    item["sources"].append(
        {
            "scope_type": source_scope_type,
            "scope_id": source_scope_id,
            "kind": kind,
            "enabled": enabled,
            "metadata": metadata,
        }
    )


def _add_callable_target_source(
    callable_items: dict[str, dict[str, Any]],
    *,
    callable_key: str,
    target_type: str,
    source_scope_type: str,
    source_scope_id: str,
    enabled: bool,
    metadata: dict[str, Any] | None = None,
) -> None:
    item = callable_items.setdefault(
        callable_key,
        {
            "callable_key": callable_key,
            "target_type": target_type,
            "effective_enabled": False,
            "effective_visible": False,
            "visibility_source": "granted",
            "sources": [],
        },
    )
    item["effective_enabled"] = bool(item["effective_enabled"] or enabled)
    item["sources"].append(
        {
            "scope_type": source_scope_type,
            "scope_id": source_scope_id,
            "kind": "grant",
            "enabled": enabled,
            "metadata": metadata,
        }
    )


def _summarize_route_group_visibility(item: dict[str, Any], *, direct_scope_type: str) -> None:
    sources = [source for source in item.get("sources", []) if isinstance(source, dict)]
    if direct_scope_type == "organization":
        has_owned = any(source.get("kind") == "owned" for source in sources)
        has_grant = any(source.get("kind") == "grant" for source in sources)
        if has_owned and has_grant:
            item["visibility_source"] = "owned_and_granted"
        elif has_owned:
            item["visibility_source"] = "owned"
        else:
            item["visibility_source"] = "granted"
        return

    has_direct = any(source.get("scope_type") == direct_scope_type for source in sources)
    has_inherited = any(source.get("scope_type") != direct_scope_type for source in sources)
    if has_direct and has_inherited:
        item["visibility_source"] = "inherited_and_granted"
    elif has_inherited:
        item["visibility_source"] = "inherited"
    else:
        item["visibility_source"] = "granted"


def _summarize_callable_target_visibility(item: dict[str, Any], *, direct_scope_type: str) -> None:
    sources = [source for source in item.get("sources", []) if isinstance(source, dict)]
    if direct_scope_type == "organization":
        item["visibility_source"] = "granted"
        return
    has_direct = any(source.get("scope_type") == direct_scope_type for source in sources)
    has_inherited = any(source.get("scope_type") != direct_scope_type for source in sources)
    if has_direct and has_inherited:
        item["visibility_source"] = "inherited_and_granted"
    elif has_inherited:
        item["visibility_source"] = "inherited"
    else:
        item["visibility_source"] = "granted"


def _has_source(item: dict[str, Any], *, scope_type: str, kind: str | None = None) -> bool:
    for source in item.get("sources", []):
        if not isinstance(source, dict):
            continue
        if source.get("scope_type") != scope_type:
            continue
        if kind is not None and source.get("kind") != kind:
            continue
        if not bool(source.get("enabled", True)):
            continue
        return True
    return False


def _apply_effective_visibility(
    item: dict[str, Any],
    *,
    team_mode: str | None,
    api_key_mode: str | None,
    user_mode: str | None = None,
) -> None:
    org_visible = _has_source(item, scope_type="organization")
    team_visible = org_visible
    if team_mode == "restrict":
        team_visible = org_visible and _has_source(item, scope_type="team", kind="grant")

    key_visible = team_visible
    if api_key_mode == "restrict":
        key_visible = team_visible and _has_source(item, scope_type="api_key", kind="grant")

    user_visible = key_visible
    if user_mode == "restrict":
        user_visible = key_visible and _has_source(item, scope_type="user", kind="grant")

    if user_mode is not None:
        item["effective_visible"] = user_visible
    else:
        item["effective_visible"] = key_visible if api_key_mode is not None else team_visible if team_mode is not None else org_visible


async def build_asset_visibility_preview(
    request: Request,
    *,
    organization_id: str,
    team_id: str | None = None,
    api_key_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    route_group_items: dict[str, dict[str, Any]] = {}
    callable_items: dict[str, dict[str, Any]] = {}
    callable_catalog_by_key = callable_catalog(request)
    route_group_repo = route_group_repository(request)
    all_route_groups = await _list_all_route_groups(route_group_repo)
    route_groups_by_key = {str(group.group_key): group for group in all_route_groups if str(group.group_key or "").strip()}
    route_group_keys = set(route_groups_by_key.keys())
    team_policy_mode = await get_scope_callable_target_policy_mode(
        request,
        scope_type="team",
        scope_id=team_id,
    )
    api_key_policy_mode = await get_scope_callable_target_policy_mode(
        request,
        scope_type="api_key",
        scope_id=api_key_id,
    )

    owned_route_groups = await list_owned_route_groups(
        request,
        owner_scope_type="organization",
        owner_scope_id=organization_id,
    )
    for item in owned_route_groups:
        _add_route_group_source(
            route_group_items,
            group_key=item["group_key"],
            source_scope_type="organization",
            source_scope_id=organization_id,
            kind="owned",
            enabled=bool(item.get("enabled", True)),
            owner_scope_type=item.get("owner_scope_type"),
            owner_scope_id=item.get("owner_scope_id"),
        )

    async def _apply_scope_callable_target_bindings(scope_type: str, scope_id: str) -> list[dict[str, Any]]:
        bindings = await list_scope_callable_target_bindings(request, scope_type=scope_type, scope_id=scope_id)
        for binding in bindings:
            callable_key = str(binding.get("callable_key") or "")
            if not callable_key:
                continue
            target = _target_for_callable_key(
                callable_key=callable_key,
                callable_catalog_by_key=callable_catalog_by_key,
                route_group_keys=route_group_keys,
            )
            _add_callable_target_source(
                callable_items,
                callable_key=callable_key,
                target_type=target.target_type,
                source_scope_type=scope_type,
                source_scope_id=scope_id,
                enabled=bool(binding.get("enabled", True)),
                metadata=binding.get("metadata"),
            )
            if target.target_type != "route_group":
                continue
            group = route_groups_by_key.get(callable_key)
            _add_route_group_source(
                route_group_items,
                group_key=callable_key,
                source_scope_type=scope_type,
                source_scope_id=scope_id,
                kind="grant",
                enabled=bool(binding.get("enabled", True)),
                metadata=binding.get("metadata"),
                owner_scope_type=getattr(group, "owner_scope_type", None),
                owner_scope_id=getattr(group, "owner_scope_id", None),
            )
        return bindings

    await _apply_scope_callable_target_bindings("organization", organization_id)

    direct_scope_type = "organization"
    direct_scope_id = organization_id
    if team_id:
        direct_scope_type = "team"
        direct_scope_id = team_id
        await _apply_scope_callable_target_bindings("team", team_id)

    if api_key_id:
        direct_scope_type = "api_key"
        direct_scope_id = api_key_id
        await _apply_scope_callable_target_bindings("api_key", api_key_id)

    user_route_group_bindings: list[dict[str, Any]] = []
    user_callable_target_bindings: list[dict[str, Any]] = []
    if user_id:
        direct_scope_type = "user"
        direct_scope_id = user_id
        user_callable_target_bindings = await _apply_scope_callable_target_bindings("user", user_id)
        user_route_group_bindings = [
            binding
            for binding in user_callable_target_bindings
            if _target_for_callable_key(
                callable_key=str(binding.get("callable_key") or ""),
                callable_catalog_by_key=callable_catalog_by_key,
                route_group_keys=route_group_keys,
            ).target_type
            == "route_group"
        ]

    user_route_group_mode = "restrict" if user_route_group_bindings else None
    user_callable_target_mode = "restrict" if user_callable_target_bindings else None

    for item in route_group_items.values():
        _summarize_route_group_visibility(item, direct_scope_type=direct_scope_type)
        _apply_effective_visibility(
            item,
            team_mode=team_policy_mode,
            api_key_mode=api_key_policy_mode,
            user_mode=user_route_group_mode,
        )
        item["sources"] = sorted(
            item["sources"],
            key=lambda source: (str(source.get("scope_type") or ""), str(source.get("scope_id") or ""), str(source.get("kind") or "")),
        )

    for item in callable_items.values():
        _summarize_callable_target_visibility(item, direct_scope_type=direct_scope_type)
        _apply_effective_visibility(
            item,
            team_mode=team_policy_mode,
            api_key_mode=api_key_policy_mode,
            user_mode=user_callable_target_mode,
        )
        item["sources"] = sorted(
            item["sources"],
            key=lambda source: (str(source.get("scope_type") or ""), str(source.get("scope_id") or "")),
        )

    return {
        "organization_id": organization_id,
        "team_id": team_id,
        "api_key_id": api_key_id,
        "user_id": user_id,
        "direct_scope_type": direct_scope_type,
        "direct_scope_id": direct_scope_id,
        "scope_policies": {
            "team": team_policy_mode or "inherit",
            "api_key": api_key_policy_mode or "inherit",
        },
        "route_groups": {
            "total": len(route_group_items),
            "items": sorted(route_group_items.values(), key=lambda item: item["group_key"]),
        },
        "callable_targets": {
            "total": len(callable_items),
            "items": sorted(callable_items.values(), key=lambda item: item["callable_key"]),
        },
    }
