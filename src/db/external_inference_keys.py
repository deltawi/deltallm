from __future__ import annotations

from datetime import UTC, datetime

from src.db.platform_accounts import PlatformAccountDatabase
from src.models.external_auth import ExternalInferenceKeyResponse, ExternalWorkspaceContext


class ExternalInferenceKeyRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def select_owned(
        self,
        token_hash: str,
        *,
        account_id: str,
        workspace: ExternalWorkspaceContext,
        include_expired: bool = False,
    ) -> ExternalInferenceKeyResponse | None:
        rows = await self.db.query_raw(
            """SELECT key.key_name, key.expires
            FROM deltallm_verificationtoken key
            JOIN deltallm_usertable runtime ON runtime.user_id = key.user_id AND runtime.team_id = key.team_id
            JOIN deltallm_teamtable team ON team.team_id = key.team_id
            JOIN deltallm_organizationtable organization ON organization.organization_id = team.organization_id
            WHERE key.token = $1 AND key.owner_account_id = $2 AND key.owner_service_account_id IS NULL
              AND key.user_id = $3 AND key.team_id = $4 AND team.organization_id = $5
              AND organization.lifecycle_state = 'active' AND ($6::bool OR key.expires IS NULL OR key.expires > NOW())
            LIMIT 1""",
            token_hash,
            account_id,
            workspace.inference_user_id,
            workspace.team_id,
            workspace.organization_id,
            include_expired,
        )
        if not rows:
            return None
        expiry = rows[0]["expires"]
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if isinstance(expiry, datetime) and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return ExternalInferenceKeyResponse(
            key_name=rows[0]["key_name"],
            expires_at=expiry,
            account_id=account_id,
            inference_user_id=workspace.inference_user_id,
            team_id=workspace.team_id,
        )
