from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request

from src.db.route_group_identity import RouteGroupIdentity
from src.db.route_groups import RouteGroupRepository


@dataclass(frozen=True)
class RouteGroupRequestContext:
    group_key: str
    repository: RouteGroupRepository


def route_group_context(request: Request) -> RouteGroupRequestContext | None:
    return getattr(request.state, "route_group_context", None)


def route_group_repository(request: Request) -> RouteGroupRepository:
    context = route_group_context(request)
    if context is not None:
        return context.repository
    repository = getattr(request.app.state, "route_group_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Route group repository unavailable")
    return repository


async def resolve_route_group_id(request: Request, route_group_id: UUID) -> str:
    repository = route_group_repository(request)
    group = await repository.get_group_by_id(str(route_group_id))
    if group is None:
        raise HTTPException(status_code=404, detail="Route group not found")
    request.state.route_group_context = RouteGroupRequestContext(
        group_key=group.group_key,
        repository=repository.for_identity(
            RouteGroupIdentity(group.group_key, group.route_group_id)
        ),
    )
    return group.group_key
