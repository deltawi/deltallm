from __future__ import annotations

from fastapi import APIRouter, Request

from src.api.admin.auth_scope import get_auth_scope
from src.api.external_auth_edge import ExternalAuthRoute, external_runtime
from src.auth.roles import Permission
from src.services.key_revocation_status import KeyRevocationStatus, KeyRevocationStatusService

router = APIRouter(tags=["keys"], route_class=ExternalAuthRoute)


@router.get("/ui/api/key-revocations/{invalidation_id}", response_model=KeyRevocationStatus)
async def revocation_status(request: Request, invalidation_id: str) -> KeyRevocationStatus:
    scope = get_auth_scope(
        request,
        request.headers.get("authorization"),
        request.headers.get("x-master-key"),
        required_permission=Permission.KEY_READ,
    )
    runtime = external_runtime(request)
    return await KeyRevocationStatusService(
        runtime.transactions, request.app.state.key_service.auth_cache_ttl_seconds
    ).read(invalidation_id, scope.account_id or "master_key", administrator=scope.is_platform_admin)
