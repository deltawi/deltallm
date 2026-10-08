from __future__ import annotations

from datetime import UTC, datetime
from pydantic import BaseModel, field_validator

from src.db.platform_accounts import PlatformAccountDatabase


class KeyRevocationRecord(BaseModel):
    status: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_created_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class KeyRevocationStatusRepository:
    def __init__(self, db: PlatformAccountDatabase) -> None:
        self.db = db

    async def read(
        self, invalidation_id: str, actor_id: str, *, administrator: bool
    ) -> KeyRevocationRecord | None:
        rows = await self.db.query_raw(
            """SELECT status, created_at FROM deltallm_cacheinvalidationoutbox
            WHERE invalidation_id = $1 AND scope_type = 'key_hash'
              AND metadata->>'auth_revocation' = 'true'
              AND ($3::bool OR metadata->>'actor_id' = $2)""",
            invalidation_id,
            actor_id,
            administrator,
        )
        return KeyRevocationRecord.model_validate(rows[0]) if rows else None
