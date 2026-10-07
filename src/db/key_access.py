from __future__ import annotations
from src.db.platform_accounts import PlatformAccountDatabase


class KeyAccessRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def notification(self, token_hash: str) -> dict[str, object] | None:
        rows = await self.db.query_raw(
            """
            SELECT
                vt.token,
                vt.key_name,
                vt.team_id,
                t.team_alias,
                t.organization_id,
                vt.owner_account_id,
                vt.owner_service_account_id,
                sa.name AS owner_service_account_name
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
            LEFT JOIN deltallm_serviceaccount sa ON vt.owner_service_account_id = sa.service_account_id
            WHERE vt.token = $1
            LIMIT 1
            """,
            token_hash,
        )
        if not rows:
            return None
        return dict(rows[0])

    async def scope(self, token_hash: str) -> dict[str, object] | None:
        rows = await self.db.query_raw(
            """
            SELECT
                vt.token,
                vt.user_id,
                COALESCE(vt.team_id, u.team_id) AS team_id,
                t.organization_id
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_usertable u ON u.user_id = vt.user_id
            LEFT JOIN deltallm_teamtable t ON t.team_id = COALESCE(vt.team_id, u.team_id)
            WHERE vt.token = $1
            LIMIT 1
            """,
            token_hash,
        )
        if not rows:
            return None
        return dict(rows[0])

    async def lock(
        self,
        token_hash: str,
    ) -> dict[str, object] | None:
        rows = await self.db.query_raw(
            """
            SELECT
                vt.token,
                vt.user_id,
                COALESCE(vt.team_id, u.team_id, sa.team_id) AS team_id,
                t.organization_id
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_usertable u ON u.user_id = vt.user_id
            LEFT JOIN deltallm_serviceaccount sa
              ON sa.service_account_id = vt.owner_service_account_id
            LEFT JOIN deltallm_teamtable t
              ON t.team_id = COALESCE(vt.team_id, u.team_id, sa.team_id)
            WHERE vt.token = $1
            LIMIT 1
            FOR UPDATE OF vt
            """,
            token_hash,
        )
        if not rows:
            return None
        row = dict(rows[0])
        return row

    async def owner(self, token_hash: str) -> str | None:
        rows = await self.db.query_raw(
            "SELECT owner_account_id FROM deltallm_verificationtoken WHERE token = $1 LIMIT 1",
            token_hash,
        )
        return str(rows[0].get("owner_account_id") or "") if rows else None
