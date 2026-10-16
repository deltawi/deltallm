from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import HTTPException, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit
from src.api.admin.endpoints.route_groups import authorize_route_group_for_request
from src.api.admin.route_group_dependencies import route_group_repository
from src.audit.actions import AuditAction
from src.db.routing.route_policy_lifecycle import RoutePolicyStateConflictError


async def discard_route_group_policy_draft(request: Request, group_key: str) -> dict[str, Any]:
    """Delete the group's draft policy. The published policy and runtime do not change."""
    request_start = perf_counter()
    repository = route_group_repository(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    await authorize_route_group_for_request(request, group, write=True)
    try:
        draft = await repository.discard_draft_policy(group_key)
    except RoutePolicyStateConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No draft policy to discard"
        )
    response = {"group_key": group_key, "discarded": True, "discarded_version": draft.version}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_policy_draft",
        resource_id=group_key,
        before={"version": draft.version, "policy_json": draft.policy_json},
        response_payload=response,
    )
    return response
