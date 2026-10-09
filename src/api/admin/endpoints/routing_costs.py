from datetime import datetime
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from src.api.admin.endpoints.common import db_or_503, get_auth_scope
from src.api.admin.spend_reporting_dependencies import (
    _reporting_cache,
    _resolve_reporting_visibility,
    _run_uncached_reporting_response,
)
from src.db.routing_costs import routing_cost_query
from src.middleware.admin import require_any_admin_permission
from src.services.routing_cost_reports import RoutingCostPage, load_routing_cost_page
from src.services.spend_visibility import SPEND_VISIBILITY_PERMISSIONS

router = APIRouter(tags=["Spend"])


@router.get(
    "/ui/api/spend/routing-costs",
    response_model=RoutingCostPage,
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Outside the authorized spend scope"},
        422: {"description": "Invalid range, filter or cursor"},
        503: {"description": "Reporting unavailable, timed out or at capacity"},
    },
)
async def routing_cost_report(
    request: Request,
    response: Response,
    start: datetime,
    end: datetime,
    limit: int = Query(default=100, ge=1, le=1000),
    model_group: str | None = Query(default=None, min_length=1, max_length=256),
    before_created_at: datetime | None = None,
    before_operation_id: UUID | None = None,
    view: Literal["organization", "team", "self"] | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> RoutingCostPage:
    scope = get_auth_scope(
        request, authorization, x_master_key, any_permission=list(SPEND_VISIBILITY_PERMISSIONS)
    )
    visibility = _resolve_reporting_visibility(request, scope, view)
    if (before_created_at is None) != (before_operation_id is None):
        raise HTTPException(status_code=422, detail="Both cursor fields are required")
    if before_created_at is not None and before_created_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="Cursor timestamp must include its timezone")
    try:
        query = routing_cost_query(
            visibility=visibility,
            start=start,
            end=end,
            limit=limit,
            model_group=model_group,
            before=(before_created_at, str(before_operation_id)) if before_created_at else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    database = db_or_503(request)
    allocation = await _reporting_cache(request)

    async def load() -> dict[str, object]:
        page = await load_routing_cost_page(database, allocation, query)
        return page.model_dump(mode="json")

    result = await _run_uncached_reporting_response(cache=allocation, loader=load)
    response.headers["Cache-Control"] = "no-store"
    return RoutingCostPage.model_validate_json(json.dumps(result))
