"""Revoke the API keys a person owns when they lose access to the key's team."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from src.api.admin.auth_scope import AuthScope
from src.api.admin.endpoints.common import emit_admin_mutation_audit
from src.audit.actions import AuditAction

logger = logging.getLogger(__name__)


async def delete_keys_owned_in_team(tx: Any, *, account_id: str, team_id: str) -> list[str]:
    return await _delete_owned_keys(tx, "team_id = $2", account_id, team_id)


async def delete_keys_owned_in_organization(
    tx: Any, *, account_id: str, organization_id: str
) -> list[str]:
    return await _delete_owned_keys(
        tx,
        "team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id = $2)",
        account_id,
        organization_id,
    )


async def delete_all_owned_keys(tx: Any, *, account_id: str) -> list[str]:
    return await _delete_owned_keys(tx, "TRUE", account_id)


async def _delete_owned_keys(tx: Any, team_filter: str, *params: str) -> list[str]:
    rows = await tx.query_raw(
        f"""
        DELETE FROM deltallm_verificationtoken
        WHERE owner_account_id = $1 AND {team_filter}
        RETURNING token
        """,
        *params,
    )
    return [str(row["token"]) for row in rows or [] if row.get("token")]


async def finish_owned_key_revocation(
    request: Request,
    *,
    request_start: float | None = None,
    scope: AuthScope | None,
    token_hashes: list[str],
    removed_account_id: str,
    context: dict[str, str],
) -> None:
    """Invalidate cached auth and audit each key after the removal transaction commits."""

    if not token_hashes:
        return
    try:
        key_service = request.app.state.key_service
        for token_hash in token_hashes:
            await key_service.invalidate_key_cache_by_hash(token_hash)
    except Exception:
        logger.exception("failed to invalidate key cache after membership removal auto-revoke")
    for token_hash in token_hashes:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_KEY_AUTO_REVOKE_MEMBERSHIP_REMOVED,
            scope=scope,
            resource_type="api_key",
            resource_id=token_hash,
            response_payload={**context, "removed_account_id": removed_account_id},
        )
