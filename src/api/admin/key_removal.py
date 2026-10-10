from __future__ import annotations
from typing import Literal
from fastapi import Request
from src.api.admin.auth_scope import AuthScope
from src.auth.roles import Permission
from src.db.identity.platform_accounts import PlatformAccountDatabase


async def remove_key_with_required_audit(
    request: Request, scope: AuthScope, token_hash: str, *, deleted: bool
) -> dict[str, object]:
    from src.api.admin import key_mutations as keys
    from src.services.key_removal import KeyRemovalService

    runtime = request.app.state.external_auth_runtime
    notification = None

    async def approve(db: PlatformAccountDatabase) -> Literal["admin", "self_service"]:
        nonlocal notification
        await keys._lock_key_organization_for_mutation(db, token_hash)
        mode = await keys._require_key_access(
            scope, db, token_hash, admin_permission=Permission.KEY_REVOKE, allow_self_service=True
        )
        notification = await keys._get_key_notification_row(db, token_hash)
        return mode

    async with runtime.operation("admin"):
        result = await KeyRemovalService(
            runtime.transactions, runtime.audit, request.app.state.key_service
        ).remove(
            token_hash,
            actor_id=scope.account_id or "master_key",
            correlation_id=runtime.correlation_id(),
            deleted=deleted,
            approve=approve,
        )
    if notification is not None and result.removed:
        await keys._notify_key_lifecycle(
            request,
            event_kind="api_key_deleted" if deleted else "api_key_revoked",
            actor_account_id=scope.account_id,
            record=keys._notification_record(notification),
        )
    return {
        "deleted" if deleted else "revoked": result.removed,
        "enforcement": result.enforcement,
        "invalidation_id": result.invalidation_id,
        "maximum_enforcement_delay_seconds": request.app.state.key_service.auth_cache_ttl_seconds
        + 1,
    }
