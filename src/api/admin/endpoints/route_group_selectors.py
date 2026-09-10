from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.api.admin.route_group_dependencies import resolve_route_group_id
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.router.runtime_generation import require_routing_runtime_generation
from src.services.selector_inventory import SelectorOptionsPage, selector_options

router = APIRouter(tags=["Admin Route Groups"])


@router.get(
    "/ui/api/route-groups/by-id/{route_group_id:uuid}/selector-options",
    response_model=SelectorOptionsPage,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))],
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
    del group_key  # The ID dependency establishes existence and pins repository identity.
    try:
        runtime = require_routing_runtime_generation(request.app.state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Routing inventory unavailable") from exc
    response.headers["Cache-Control"] = "no-store"
    return selector_options(
        runtime.deployment_registry.physical_deployments,
        search=search,
        selected_id=selected_id,
        limit=limit,
        offset=offset,
    )
