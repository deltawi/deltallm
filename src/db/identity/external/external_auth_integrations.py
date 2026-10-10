from __future__ import annotations

from src.db.identity.platform_accounts import PlatformAccountDatabase
from src.db.identity.external.external_auth_records import ExternalIntegrationRecord


class ExternalIntegrationRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def register_configured(self, ids: list[str]) -> None:
        await self.db.execute_raw(
            """
            INSERT INTO deltallm_externalauthintegration (integration_id, updated_at)
            SELECT unnest($1::text[]), NOW() ON CONFLICT DO NOTHING
            """,
            ids,
        )

    async def lock(
        self, integration_id: str, *, write: bool = False
    ) -> ExternalIntegrationRecord | None:
        lock_mode = "FOR UPDATE" if write else "FOR SHARE"
        rows = await self.db.query_raw(
            f"SELECT * FROM deltallm_externalauthintegration WHERE integration_id = $1 {lock_mode}",
            integration_id,
        )
        return ExternalIntegrationRecord.model_validate(rows[0]) if rows else None

    async def set_enabled(
        self, integration_id: str, *, enabled: bool, version: int
    ) -> ExternalIntegrationRecord | None:
        rows = await self.db.query_raw(
            """
            UPDATE deltallm_externalauthintegration SET enabled = $2, version = version + 1,
                epoch = epoch + CASE WHEN NOT $2 THEN 1 ELSE 0 END, updated_at = NOW()
            WHERE integration_id = $1 AND version = $3 RETURNING *
            """,
            integration_id,
            enabled,
            version,
        )
        return ExternalIntegrationRecord.model_validate(rows[0]) if rows else None
