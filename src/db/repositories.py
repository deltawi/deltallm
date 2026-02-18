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
    user_tpm_limit: int | None = None
    user_rpm_limit: int | None = None
    team_tpm_limit: int | None = None
    team_rpm_limit: int | None = None
    org_tpm_limit: int | None = None
    org_rpm_limit: int | None = None
    max_parallel_requests: int | None = None
    organization_id: str | None = None
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
            SELECT
                v.token,
                v.key_name,
                v.user_id,
                COALESCE(v.team_id, u.team_id) AS team_id,
                t.organization_id,
                v.models,
                v.max_budget,
                v.spend,
                v.tpm_limit AS key_tpm_limit,
                v.rpm_limit AS key_rpm_limit,
                u.tpm_limit AS user_tpm_limit,
                u.rpm_limit AS user_rpm_limit,
                t.tpm_limit AS team_tpm_limit,
                t.rpm_limit AS team_rpm_limit,
                o.tpm_limit AS org_tpm_limit,
                o.rpm_limit AS org_rpm_limit,
                v.max_parallel_requests,
                v.metadata,
                v.expires
            FROM litellm_verificationtoken v
            LEFT JOIN litellm_usertable u
                ON u.user_id = v.user_id
            LEFT JOIN litellm_teamtable t
                ON t.team_id = COALESCE(v.team_id, u.team_id)
            LEFT JOIN litellm_organizationtable o
                ON o.organization_id = t.organization_id
            WHERE v.token = $1
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
            tpm_limit=row.get("key_tpm_limit"),
            rpm_limit=row.get("key_rpm_limit"),
            user_tpm_limit=row.get("user_tpm_limit"),
            user_rpm_limit=row.get("user_rpm_limit"),
            team_tpm_limit=row.get("team_tpm_limit"),
            team_rpm_limit=row.get("team_rpm_limit"),
            org_tpm_limit=row.get("org_tpm_limit"),
            org_rpm_limit=row.get("org_rpm_limit"),
            max_parallel_requests=row.get("max_parallel_requests"),
            organization_id=row.get("organization_id"),
            guardrails=row.get("guardrails"),
            metadata=row.get("metadata"),
            expires=expires,
        )
