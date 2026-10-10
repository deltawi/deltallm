from __future__ import annotations

import logging
from typing import Literal
from fastapi import HTTPException, Request, status
from src.api.admin.auth_scope import AuthScope
from src.api.admin.key_access_policy import _resolve_key_access_mode, _resolve_key_read_access_mode
from src.auth.roles import Permission
from src.db.identity.key_access import KeyAccessRepository
from src.db.identity.platform_accounts import PlatformAccountDatabase
from src.services.key_notifications import KeyNotificationRecord
from src.api.admin.organization_mutations import require_active_organization_mutation

logger = logging.getLogger(__name__)


def _notification_record(row: dict[str, object]) -> KeyNotificationRecord:
    return KeyNotificationRecord(
        token_hash=str(row.get("token") or ""),
        key_name=str(row.get("key_name") or ""),
        team_id=str(row.get("team_id") or "").strip() or None,
        team_alias=str(row.get("team_alias") or "").strip() or None,
        organization_id=str(row.get("organization_id") or "").strip() or None,
        owner_account_id=str(row.get("owner_account_id") or "").strip() or None,
        owner_service_account_id=str(row.get("owner_service_account_id") or "").strip() or None,
        owner_service_account_name=str(row.get("owner_service_account_name") or "").strip() or None,
    )


async def _notify_key_lifecycle(
    request: Request,
    *,
    event_kind: str,
    actor_account_id: str | None,
    record: KeyNotificationRecord,
) -> None:
    service = getattr(request.app.state, "key_notification_service", None)
    if service is None:
        return
    try:
        await service.notify_lifecycle(
            event_kind=event_kind,
            actor_account_id=actor_account_id,
            record=record,
        )
    except Exception:  # pragma: no cover - defensive guard
        logger.exception(
            "failed to enqueue key lifecycle notification", extra={"notification_kind": event_kind}
        )


async def _get_key_notification_row(
    db: PlatformAccountDatabase, token_hash: str
) -> dict[str, object] | None:
    return await KeyAccessRepository(db).notification(token_hash)


async def _get_key_scope_row(db: PlatformAccountDatabase, token_hash: str) -> dict[str, object]:
    row = await KeyAccessRepository(db).scope(token_hash)
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return row


async def _lock_key_organization_for_mutation(
    db: PlatformAccountDatabase, token_hash: str
) -> dict[str, object]:
    row = await KeyAccessRepository(db).lock(token_hash)
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    organization_id = str(row.get("organization_id") or "").strip()
    if organization_id:
        await require_active_organization_mutation(db, organization_id)
    return row


async def _require_key_access(
    scope: AuthScope,
    db: PlatformAccountDatabase,
    token_hash: str,
    *,
    admin_permission: str,
    allow_self_service: bool = False,
) -> Literal["admin", "self_service"]:
    if scope.external_workspace is not None:
        from src.db.identity.external.external_inference_keys import ExternalInferenceKeyRepository

        selected = await ExternalInferenceKeyRepository(db).select_owned(
            token_hash,
            account_id=scope.account_id,
            workspace=scope.external_workspace,
            include_expired=True,
        )
        if selected is None:
            raise HTTPException(status_code=403, detail="You can only manage your own keys")
    row = await _get_key_scope_row(db, token_hash)
    organization_id = str(row.get("organization_id") or "").strip() or None
    team_id = str(row.get("team_id") or "").strip() or None
    if admin_permission == Permission.KEY_READ:
        access_mode = _resolve_key_read_access_mode(
            scope, organization_id=organization_id, team_id=team_id
        )
    else:
        access_mode = _resolve_key_access_mode(
            scope,
            organization_id=organization_id,
            team_id=team_id,
            admin_permission=admin_permission,
            allow_self_service=allow_self_service,
        )

    if access_mode == "self_service":
        if await KeyAccessRepository(db).owner(token_hash) == scope.account_id:
            return access_mode
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only manage your own keys"
        )

    return access_mode
