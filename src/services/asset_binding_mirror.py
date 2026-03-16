from __future__ import annotations

from typing import Any

from fastapi import Request

from src.db.callable_targets import CallableTargetBindingRecord, CallableTargetBindingRepository
from src.db.route_groups import RouteGroupBindingRecord, RouteGroupRepository
from src.services.callable_targets import CallableTarget


def callable_target_binding_repository(request: Request) -> CallableTargetBindingRepository | None:
    repository = getattr(request.app.state, "callable_target_binding_repository", None)
    if repository is not None and callable(getattr(repository, "list_bindings", None)):
        return repository
    return None


def route_group_repository(request: Request) -> RouteGroupRepository | None:
    repository = getattr(request.app.state, "route_group_repository", None)
    if repository is not None and callable(getattr(repository, "list_bindings", None)):
        return repository
    return None


def callable_catalog(request: Request) -> dict[str, CallableTarget]:
    catalog = getattr(request.app.state, "callable_target_catalog", None)
    if not isinstance(catalog, dict):
        return {}
    return {str(key): value for key, value in catalog.items() if isinstance(value, CallableTarget)}


async def reload_callable_target_grants(request: Request) -> None:
    service = getattr(request.app.state, "callable_target_grant_service", None)
    if service is None or not callable(getattr(service, "reload", None)):
        return
    await service.reload()


async def list_all_callable_target_bindings(
    repository: CallableTargetBindingRepository | None,
    *,
    callable_key: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    page_size: int = 500,
) -> list[CallableTargetBindingRecord]:
    if repository is None:
        return []

    items: list[CallableTargetBindingRecord] = []
    offset = 0
    while True:
        page, total = await repository.list_bindings(
            callable_key=callable_key,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=page_size,
            offset=offset,
        )
        items.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    return items


async def list_all_route_group_bindings(
    repository: RouteGroupRepository | None,
    *,
    group_key: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    page_size: int = 500,
) -> list[RouteGroupBindingRecord]:
    if repository is None:
        return []

    items: list[RouteGroupBindingRecord] = []
    offset = 0
    while True:
        page, total = await repository.list_bindings(
            group_key=group_key,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=page_size,
            offset=offset,
        )
        items.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    return items


async def mirror_route_group_binding_to_callable_target(
    request: Request,
    *,
    group_key: str,
    scope_type: str,
    scope_id: str,
    enabled: bool,
    metadata: dict[str, Any] | None,
) -> CallableTargetBindingRecord | None:
    repository = callable_target_binding_repository(request)
    if repository is None:
        return None
    return await repository.upsert_binding(
        callable_key=group_key,
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=enabled,
        metadata=metadata,
    )


async def delete_callable_target_binding_mirror(
    request: Request,
    *,
    callable_key: str,
    scope_type: str,
    scope_id: str,
) -> int:
    repository = callable_target_binding_repository(request)
    if repository is None:
        return 0
    bindings = await list_all_callable_target_bindings(
        repository,
        callable_key=callable_key,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    deleted = 0
    for binding in bindings:
        if await repository.delete_binding(binding.callable_target_binding_id):
            deleted += 1
    return deleted


async def delete_all_callable_target_bindings_for_key(request: Request, *, callable_key: str) -> int:
    repository = callable_target_binding_repository(request)
    if repository is None:
        return 0
    bindings = await list_all_callable_target_bindings(repository, callable_key=callable_key)
    deleted = 0
    for binding in bindings:
        if await repository.delete_binding(binding.callable_target_binding_id):
            deleted += 1
    return deleted


async def mirror_callable_target_binding_to_route_group(
    request: Request,
    *,
    callable_key: str,
    scope_type: str,
    scope_id: str,
    enabled: bool,
    metadata: dict[str, Any] | None,
) -> RouteGroupBindingRecord | None:
    repository = route_group_repository(request)
    if repository is None:
        return None
    if await repository.get_group(callable_key) is None:
        return None
    return await repository.upsert_binding(
        callable_key,
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=enabled,
        metadata=metadata,
    )


async def delete_route_group_binding_mirror(
    request: Request,
    *,
    group_key: str,
    scope_type: str,
    scope_id: str,
) -> int:
    repository = route_group_repository(request)
    if repository is None:
        return 0
    bindings = await list_all_route_group_bindings(
        repository,
        group_key=group_key,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    deleted = 0
    for binding in bindings:
        if await repository.delete_binding(binding.route_group_binding_id):
            deleted += 1
    return deleted
