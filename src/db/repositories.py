from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class KeyRecord:
    token: str
    key_name: str | None = None
    user_id: str | None = None
    team_id: str | None = None
    models: list[str] | None = None
    max_budget: float | None = None
    spend: float = 0.0
    tpm_limit: int | None = None
    rpm_limit: int | None = None
    max_parallel_requests: int | None = None
    guardrails: list[str] | None = None
    metadata: dict[str, Any] | None = None
    expires: datetime | None = None


class KeyRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def get_by_token(self, token_hash: str) -> KeyRecord | None:
        if self.prisma is None:
            return None

        # Fallback to raw SQL so repository works before Prisma model generation.
        rows = await self.prisma.query_raw(
            """
            SELECT token, key_name, user_id, team_id, models, max_budget, spend,
                   tpm_limit, rpm_limit, max_parallel_requests, metadata, expires
            FROM litellm_verificationtoken
            WHERE token = $1
            LIMIT 1
            """,
            token_hash,
        )
        if not rows:
            return None

        row = rows[0]
        expires = row.get("expires")
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires.replace("Z", "+00:00")).astimezone(UTC)

        return KeyRecord(
            token=row["token"],
            key_name=row.get("key_name"),
            user_id=row.get("user_id"),
            team_id=row.get("team_id"),
            models=row.get("models") or [],
            max_budget=row.get("max_budget"),
            spend=float(row.get("spend") or 0.0),
            tpm_limit=row.get("tpm_limit"),
            rpm_limit=row.get("rpm_limit"),
            max_parallel_requests=row.get("max_parallel_requests"),
            guardrails=row.get("guardrails"),
            metadata=row.get("metadata"),
            expires=expires,
        )
