from __future__ import annotations

from src.db.identity.platform_accounts import PlatformAccountDatabase


class ExternalAuthRollbackRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def counts(self) -> dict[str, int]:
        rows = await self.db.query_raw("""SELECT
            (SELECT count(*)::int FROM deltallm_externalauthintegration WHERE enabled) AS enabled_integrations,
            (SELECT count(*)::int FROM (SELECT 1 FROM deltallm_externalauthparentsession WHERE revoked_at IS NULL LIMIT 1001) sample) AS live_parents,
            (SELECT count(*)::int FROM (SELECT 1 FROM deltallm_platformsession WHERE external_parent_id IS NOT NULL AND revoked_at IS NULL LIMIT 1001) sample) AS live_children,
            (SELECT count(*)::int FROM (SELECT 1 FROM deltallm_cacheinvalidationoutbox WHERE scope_type = 'key_hash' AND metadata->>'auth_revocation' = 'true' LIMIT 1001) sample) AS retained_revocations""")
        return {str(key): int(value) for key, value in rows[0].items()}

    async def disable(self) -> None:
        await self.db.execute_raw("""UPDATE deltallm_externalauthintegration SET enabled = false,
            epoch = epoch + 1, version = version + 1, updated_at = NOW() WHERE enabled""")

    async def revoke_page(self, *, parents: bool, limit: int = 1000) -> int:
        table = "deltallm_externalauthparentsession" if parents else "deltallm_platformsession"
        column = "parent_id" if parents else "session_id"
        predicate = (
            "revoked_at IS NULL"
            if parents
            else "revoked_at IS NULL AND external_parent_id IS NOT NULL"
        )
        rows = await self.db.query_raw(
            f"""WITH candidates AS (
            SELECT {column} FROM {table} WHERE {predicate}
            ORDER BY {column} LIMIT $1 FOR UPDATE SKIP LOCKED
        ), revoked AS (
            UPDATE {table} target SET revoked_at = NOW(), updated_at = NOW()
            FROM candidates WHERE target.{column} = candidates.{column} RETURNING 1
        ) SELECT count(*)::int AS revoked FROM revoked""",
            limit,
        )
        return int(rows[0]["revoked"])

    async def revoked_hashes(self, after: str, *, limit: int = 500) -> list[tuple[str, str]]:
        rows = await self.db.query_raw(
            """SELECT invalidation_id, scope_id
            FROM deltallm_cacheinvalidationoutbox WHERE scope_type = 'key_hash'
              AND metadata->>'auth_revocation' = 'true' AND invalidation_id > $1
            ORDER BY invalidation_id LIMIT $2""",
            after,
            limit,
        )
        return [(str(row["invalidation_id"]), str(row["scope_id"])) for row in rows]
