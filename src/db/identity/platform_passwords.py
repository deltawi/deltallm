from __future__ import annotations

from dataclasses import dataclass

from src.db.identity.platform_accounts import PlatformAccountDatabase


@dataclass(frozen=True, slots=True)
class PlatformPasswordRecord:
    password_hash: str | None


class PlatformPasswordRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def get(self, account_id: str) -> PlatformPasswordRecord | None:
        rows = await self.db.query_raw(
            "SELECT password_hash FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1",
            account_id,
        )
        if not rows:
            return None
        stored = rows[0].get("password_hash")
        return PlatformPasswordRecord(stored if isinstance(stored, str) else None)

    async def replace(
        self, account_id: str, *, expected_hash: str | None, password_hash: str
    ) -> bool:
        changed = await self.db.execute_raw(
            """
            UPDATE deltallm_platformaccount
            SET password_hash = $1, force_password_change = false, updated_at = NOW()
            WHERE account_id = $2 AND password_hash IS NOT DISTINCT FROM $3::text
            """,
            password_hash,
            account_id,
            expected_hash,
        )
        return changed == 1
