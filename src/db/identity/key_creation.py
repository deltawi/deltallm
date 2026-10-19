from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from prisma.errors import RawQueryError


class KeyCreationDatabase(Protocol):
    async def execute_raw(self, query: str, *params: object) -> int: ...


@dataclass(frozen=True)
class KeyCreateRecord:
    token_hash: str
    key_name: str
    user_id: str | None
    team_id: str
    owner_account_id: str | None
    owner_service_account_id: str | None
    max_budget: float | None
    rpm_limit: int | None
    tpm_limit: int | None
    rph_limit: int | None
    rpd_limit: int | None
    tpd_limit: int | None
    expires: str | None
    budget_duration: str | None
    budget_reset_at: datetime | str | None
    metadata_json: str | None


class KeyCredentialConflict(Exception):
    pass


class KeyCreationRepository:
    def __init__(self, db: KeyCreationDatabase) -> None:
        self.db = db

    async def create(self, record: KeyCreateRecord) -> None:
        # The database trigger reserves the hash in the same transaction.
        try:
            await self.db.execute_raw(
                """
                INSERT INTO deltallm_verificationtoken (
                    id, token, key_name, user_id, team_id, owner_account_id,
                    owner_service_account_id, spend, max_budget, rpm_limit,
                    tpm_limit, rph_limit, rpd_limit, tpd_limit, expires,
                    budget_duration, budget_reset_at, metadata, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1, $2, $3, $4, $5, $6, 0, $7, $8,
                    $9, $10, $11, $12, $13::timestamp, $14, $15::timestamp,
                    $16::jsonb, NOW(), NOW()
                )
                """,
                record.token_hash,
                record.key_name,
                record.user_id,
                record.team_id,
                record.owner_account_id,
                record.owner_service_account_id,
                record.max_budget,
                record.rpm_limit,
                record.tpm_limit,
                record.rph_limit,
                record.rpd_limit,
                record.tpd_limit,
                record.expires,
                record.budget_duration,
                record.budget_reset_at,
                record.metadata_json,
            )
        except RawQueryError as exc:
            if isinstance(exc.meta, dict) and exc.meta.get("code") == "PKEY1":
                raise KeyCredentialConflict() from None
            raise
