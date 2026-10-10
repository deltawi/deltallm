from __future__ import annotations

from src.db.identity.platform_accounts import PlatformAccountDatabase


class KeyRemovalRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def remove(self, token_hash: str) -> bool:
        return bool(
            await self.db.execute_raw(
                "DELETE FROM deltallm_verificationtoken WHERE token = $1", token_hash
            )
        )
