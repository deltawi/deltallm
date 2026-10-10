from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.admin.endpoints import route_groups as operations
from src.api.admin.endpoints.models import scoped_model_entries_for_principal
from src.api.admin.route_group_dependencies import resolve_route_group_id
from src.api.admin.route_group_dependencies import route_group_repository
from src.middleware.admin import require_authenticated
from src.router.runtime_generation import require_routing_runtime_generation
from src.services.routing.selector_inventory import SelectorOptionsPage, selector_options

router = APIRouter(tags=["Admin Route Groups"])


@router.get(
    "/ui/api/route-groups/by-id/{route_group_id:uuid}/selector-options",
    response_model=SelectorOptionsPage,
    dependencies=[Depends(require_authenticated)],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Configuration permission required"},
        404: {"description": "Model group not found"},
        503: {"description": "Routing inventory unavailable"},
    },
)
async def list_selector_options(
    request: Request,
    response: Response,
    group_key: Annotated[str, Depends(resolve_route_group_id)],
    search: Annotated[str, Query(max_length=128)] = "",
    selected_id: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> SelectorOptionsPage:
    group = await route_group_repository(request).get_group(group_key)
    if group is None:
        raise HTTPException(status_code=404, detail="Model group not found")
    principal, _ = await operations.authorize_route_group_for_request(request, group)
    try:
        runtime = require_routing_runtime_generation(request.app.state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Routing inventory unavailable") from exc
    response.headers["Cache-Control"] = "no-store"
    visible_deployment_ids = {
        str(entry.get("deployment_id") or "")
        for entry in await scoped_model_entries_for_principal(request, principal)
    }
    visible_deployments = {
        deployment_id: deployment
        for deployment_id, deployment in runtime.deployment_registry.physical_deployments.items()
        if deployment_id in visible_deployment_ids
    }
    return selector_options(
        visible_deployments,
        search=search,
        selected_id=selected_id,
        limit=limit,
        offset=offset,
    )
