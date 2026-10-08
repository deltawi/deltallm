from __future__ import annotations

from src.auth.external_contracts import VerifiedExternalAssertion
from src.db.platform_accounts import PlatformAccountDatabase


class ExternalAssertionRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def consume(self, assertion: VerifiedExternalAssertion) -> str:
        rows = await self.db.query_raw(
            """
            WITH integration AS MATERIALIZED (
                SELECT integration_id, enabled FROM deltallm_externalauthintegration
                WHERE integration_id = $1 FOR SHARE
            ), consumed AS (
                INSERT INTO deltallm_externalauthassertionuse (
                    integration_id, jti_hash, purpose, retain_until)
                SELECT integration_id, $2, $3, NOW() + INTERVAL '15 minutes' FROM integration WHERE enabled
                ON CONFLICT DO NOTHING RETURNING integration_id
            ) SELECT CASE WHEN EXISTS (SELECT 1 FROM consumed) THEN 'claimed'
                WHEN EXISTS (SELECT 1 FROM integration WHERE enabled) THEN 'replayed'
                ELSE 'disabled' END AS result
            """,
            assertion.integration_id,
            assertion.nonce_hash,
            assertion.claims.purpose,
        )
        return str(rows[0]["result"])
