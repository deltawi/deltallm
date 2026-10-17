from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.admin.endpoints import route_groups as operations
from src.api.admin.endpoints.route_group_policy_drafts import discard_route_group_policy_draft
from src.api.admin.request_validation import (
    BadRequestValidationRoute,
    PolicyBadRequestValidationRoute,
)
from src.api.admin.route_group_contracts import (
    RouteGroupDeleteResponse,
    RouteGroupDetailResponse,
    RouteGroupErrorResponse,
    RouteGroupMemberMutationResponse,
    RouteGroupMemberWriteRequest,
    RouteGroupMutationResponse,
    RouteGroupQualityResponse,
    RouteGroupResolutionResponse,
    RouteGroupUpdateRequest,
    RoutePolicyCurrentResponse,
    RoutePolicyDocumentRequest,
    RoutePolicyDraftDiscardResponse,
    RoutePolicyHistoryResponse,
    RoutePolicyMutationResponse,
    RoutePolicyRollbackRequest,
    RoutePolicyRollbackResponse,
    RoutePolicySimulationRequest,
    RoutePolicySimulationResponse,
    RoutePolicyValidationResponse,
)
from src.api.admin.route_group_dependencies import resolve_route_group_id, route_group_repository
from src.middleware.admin import require_authenticated
from src.router.runtime_generation import require_routing_runtime_generation
from src.services.routing.route_group_quality import build_route_group_quality

router = APIRouter(
    tags=["Admin Route Groups"],
    route_class=BadRequestValidationRoute,
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Permission denied"},
        404: {"model": RouteGroupErrorResponse},
        409: {"model": RouteGroupErrorResponse},
        503: {"model": RouteGroupErrorResponse},
        400: {"description": "Invalid request"},
    },
)

policy_router = APIRouter(route_class=PolicyBadRequestValidationRoute)

GroupKey = Annotated[str, Depends(resolve_route_group_id)]
_READ = [Depends(require_authenticated)]
_WRITE = [Depends(require_authenticated)]
_BASE = "/ui/api/route-groups/by-id/{route_group_id:uuid}"


@router.get(
    "/ui/api/route-groups/resolve/by-key",
    response_model=RouteGroupResolutionResponse,
    dependencies=_READ,
)
async def resolve_route_group_key(
    request: Request, group_key: Annotated[str, Query(min_length=1)]
) -> RouteGroupResolutionResponse:
    group = await route_group_repository(request).get_group(group_key)
    if group is None:
        raise HTTPException(status_code=404, detail="Route group not found")
    await operations.authorize_route_group_for_request(request, group)
    return RouteGroupResolutionResponse(
        route_group_id=group.route_group_id, group_key=group.group_key
    )


# These transport adapters share the established operation owners with the key
# compatibility routes. The dependency pins repository access to the requested ID.


@router.get(_BASE, response_model=RouteGroupDetailResponse, dependencies=_READ)
async def get_route_group_by_id(request: Request, group_key: GroupKey) -> RouteGroupDetailResponse:
    return RouteGroupDetailResponse.model_validate(
        await operations.get_route_group(request, group_key)
    )


@router.put(_BASE, response_model=RouteGroupMutationResponse, dependencies=_WRITE)
async def update_route_group_by_id(
    request: Request, group_key: GroupKey, payload: RouteGroupUpdateRequest
) -> RouteGroupMutationResponse:
    return RouteGroupMutationResponse.model_validate(
        await operations.update_route_group(
            request, group_key, payload.model_dump(exclude_unset=True)
        )
    )


@router.delete(_BASE, response_model=RouteGroupDeleteResponse, dependencies=_WRITE)
async def delete_route_group_by_id(
    request: Request, group_key: GroupKey
) -> RouteGroupDeleteResponse:
    return RouteGroupDeleteResponse.model_validate(
        await operations.delete_route_group(request, group_key)
    )


@router.get(
    _BASE + "/members", response_model=list[RouteGroupMemberMutationResponse], dependencies=_READ
)
async def list_route_group_members_by_id(
    request: Request, group_key: GroupKey
) -> list[RouteGroupMemberMutationResponse]:
    members = await operations.list_route_group_members(request, group_key)
    return [RouteGroupMemberMutationResponse.model_validate(member) for member in members]


@router.post(
    _BASE + "/members", response_model=RouteGroupMemberMutationResponse, dependencies=_WRITE
)
async def upsert_route_group_member_by_id(
    request: Request, group_key: GroupKey, payload: RouteGroupMemberWriteRequest
) -> RouteGroupMemberMutationResponse:
    return RouteGroupMemberMutationResponse.model_validate(
        await operations.upsert_route_group_member(
            request, group_key, payload.model_dump(exclude_unset=True)
        )
    )


@router.delete(
    _BASE + "/members/{deployment_id:path}",
    response_model=RouteGroupDeleteResponse,
    dependencies=_WRITE,
)
async def remove_route_group_member_by_id(
    request: Request, group_key: GroupKey, deployment_id: str
) -> RouteGroupDeleteResponse:
    return RouteGroupDeleteResponse.model_validate(
        await operations.delete_route_group_member(request, group_key, deployment_id)
    )


@router.get(_BASE + "/quality", response_model=RouteGroupQualityResponse, dependencies=_READ)
async def get_route_group_quality_by_id(
    request: Request, group_key: GroupKey
) -> RouteGroupQualityResponse:
    """Live answer quality per member for the last window; no database work."""

    group = await route_group_repository(request).get_group(group_key)
    if group is None:
        raise HTTPException(status_code=404, detail="Route group not found")
    await operations.authorize_route_group_for_request(request, group)
    generation = require_routing_runtime_generation(request.app.state)
    tracker = getattr(generation.router, "quality", None)
    return build_route_group_quality(
        group_key=group_key,
        runtime_group=next(
            (item for item in generation.route_groups if item.get("key") == group_key), None
        ),
        policy=generation.router_config.route_group_policies.get(group_key),
        snapshot=tracker.snapshot() if tracker is not None else None,
    )


@router.get(_BASE + "/policy", response_model=RoutePolicyCurrentResponse, dependencies=_READ)
async def get_route_group_policy_by_id(
    request: Request, group_key: GroupKey
) -> RoutePolicyCurrentResponse:
    return RoutePolicyCurrentResponse.model_validate(
        await operations.get_route_group_policy(request, group_key)
    )


@router.get(_BASE + "/policies", response_model=RoutePolicyHistoryResponse, dependencies=_READ)
async def list_route_group_policies_by_id(
    request: Request, group_key: GroupKey
) -> RoutePolicyHistoryResponse:
    return RoutePolicyHistoryResponse.model_validate(
        await operations.list_route_group_policies(request, group_key)
    )


@policy_router.post(
    _BASE + "/policy/validate",
    response_model=RoutePolicyValidationResponse,
    response_model_exclude_unset=True,
    dependencies=_WRITE,
)
async def validate_route_group_policy_by_id(
    request: Request, group_key: GroupKey, payload: RoutePolicyDocumentRequest
) -> RoutePolicyValidationResponse:
    return RoutePolicyValidationResponse.model_validate(
        await operations.validate_route_group_policy(request, group_key, payload)
    )


@policy_router.post(
    _BASE + "/policy/draft", response_model=RoutePolicyMutationResponse, dependencies=_WRITE
)
async def save_route_group_policy_draft_by_id(
    request: Request, group_key: GroupKey, payload: RoutePolicyDocumentRequest
) -> RoutePolicyMutationResponse:
    return RoutePolicyMutationResponse.model_validate(
        await operations.save_route_group_policy_draft(request, group_key, payload)
    )


@router.delete(
    _BASE + "/policy/draft", response_model=RoutePolicyDraftDiscardResponse, dependencies=_WRITE
)
async def discard_route_group_policy_draft_by_id(
    request: Request, group_key: GroupKey
) -> RoutePolicyDraftDiscardResponse:
    return RoutePolicyDraftDiscardResponse.model_validate(
        await discard_route_group_policy_draft(request, group_key)
    )


@policy_router.post(
    _BASE + "/policy/publish", response_model=RoutePolicyMutationResponse, dependencies=_WRITE
)
async def publish_route_group_policy_by_id(
    request: Request, group_key: GroupKey, payload: RoutePolicyDocumentRequest | None = None
) -> RoutePolicyMutationResponse:
    return RoutePolicyMutationResponse.model_validate(
        await operations.publish_route_group_policy_v2(request, group_key, payload)
    )


@router.post(
    _BASE + "/policy/rollback", response_model=RoutePolicyRollbackResponse, dependencies=_WRITE
)
async def rollback_route_group_policy_by_id(
    request: Request, group_key: GroupKey, payload: RoutePolicyRollbackRequest
) -> RoutePolicyRollbackResponse:
    return RoutePolicyRollbackResponse.model_validate(
        await operations.rollback_route_group_policy(
            request, group_key, payload.model_dump(exclude_unset=True)
        )
    )


@router.post(
    _BASE + "/policy/simulate", response_model=RoutePolicySimulationResponse, dependencies=_READ
)
async def simulate_route_group_policy_by_id(
    request: Request, group_key: GroupKey, payload: RoutePolicySimulationRequest | None = None
) -> RoutePolicySimulationResponse:
    return RoutePolicySimulationResponse.model_validate(
        await operations.simulate_route_group_policy(request, group_key, payload)
    )


router.include_router(policy_router)
