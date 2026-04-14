from __future__ import annotations

from typing import TYPE_CHECKING

from src.models.responses import UserAPIKeyAuth

if TYPE_CHECKING:
    from src.batch.create.models import BatchCreateSessionRecord


def batch_pending_scope_target_for_auth(
    auth: UserAPIKeyAuth,
) -> tuple[str, str, str | None, str | None] | None:
    if auth.team_id:
        return ("team", auth.team_id, None, auth.team_id)
    if auth.api_key:
        return ("api_key", auth.api_key, auth.api_key, None)
    return None


def batch_pending_scope_target_for_session(
    session: "BatchCreateSessionRecord",
) -> tuple[str, str, str | None, str | None] | None:
    if session.created_by_team_id:
        return ("team", session.created_by_team_id, None, session.created_by_team_id)
    if session.created_by_api_key:
        return ("api_key", session.created_by_api_key, session.created_by_api_key, None)
    return None


def batch_pending_scope_key_for_auth(auth: UserAPIKeyAuth) -> str | None:
    if auth.team_id:
        return f"team:{auth.team_id}"
    if auth.api_key:
        return f"api_key:{auth.api_key}"
    return None


def batch_idempotency_scope_key(auth: UserAPIKeyAuth) -> str | None:
    if auth.team_id:
        return f"team:{auth.team_id}"
    if auth.organization_id:
        return f"organization:{auth.organization_id}"
    if auth.api_key:
        return f"api_key:{auth.api_key}"
    return None
