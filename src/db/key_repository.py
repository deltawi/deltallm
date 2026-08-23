from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _parse_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


@dataclass
class KeyRecord:
    token: str
    key_name: str | None = None
    user_id: str | None = None
    team_id: str | None = None
    owner_account_id: str | None = None
    models: list[str] | None = None
    team_models: list[str] | None = None
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
    team_model_rpm_limit: dict[str, int] | None = None
    team_model_tpm_limit: dict[str, int] | None = None
    org_model_rpm_limit: dict[str, int] | None = None
    org_model_tpm_limit: dict[str, int] | None = None
    max_parallel_requests: int | None = None
    key_rph_limit: int | None = None
    key_rpd_limit: int | None = None
    key_tpd_limit: int | None = None
    user_rph_limit: int | None = None
    user_rpd_limit: int | None = None
    user_tpd_limit: int | None = None
    team_rph_limit: int | None = None
    team_rpd_limit: int | None = None
    team_tpd_limit: int | None = None
    org_rph_limit: int | None = None
    org_rpd_limit: int | None = None
    org_tpd_limit: int | None = None
    organization_id: str | None = None
    organization_lifecycle_state: str = "active"
    organization_lifecycle_version: int = 0
    organization_lifecycle_generation: int = 0
    guardrails: list[str] | None = None
    metadata: dict[str, Any] | None = None
    team_metadata: dict[str, Any] | None = None
    org_metadata: dict[str, Any] | None = None
    expires: datetime | None = None


class KeyRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def get_by_token(self, token_hash: str) -> KeyRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT
                v.token,
                v.key_name,
                v.user_id,
                v.owner_account_id,
                COALESCE(v.team_id, u.team_id, s.team_id) AS team_id,
                t.organization_id,
                CASE
                    WHEN t.organization_id IS NULL THEN 'active'
                    WHEN o.organization_id IS NULL THEN 'missing'
                    ELSE o.lifecycle_state
                END AS organization_lifecycle_state,
                COALESCE(o.lifecycle_version, 0) AS organization_lifecycle_version,
                COALESCE((
                    SELECT generation
                    FROM deltallm_organizationlifecyclegeneration
                    WHERE singleton_id = 1
                ), 0) AS organization_lifecycle_generation,
                v.models,
                t.models AS team_models,
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
                t.model_rpm_limit AS team_model_rpm_limit,
                t.model_tpm_limit AS team_model_tpm_limit,
                o.model_rpm_limit AS org_model_rpm_limit,
                o.model_tpm_limit AS org_model_tpm_limit,
                v.max_parallel_requests,
                v.rph_limit AS key_rph_limit,
                v.rpd_limit AS key_rpd_limit,
                v.tpd_limit AS key_tpd_limit,
                u.rph_limit AS user_rph_limit,
                u.rpd_limit AS user_rpd_limit,
                u.tpd_limit AS user_tpd_limit,
                t.rph_limit AS team_rph_limit,
                t.rpd_limit AS team_rpd_limit,
                t.tpd_limit AS team_tpd_limit,
                o.rph_limit AS org_rph_limit,
                o.rpd_limit AS org_rpd_limit,
                o.tpd_limit AS org_tpd_limit,
                v.metadata,
                t.metadata AS team_metadata,
                o.metadata AS org_metadata,
                v.expires
            FROM deltallm_verificationtoken v
            LEFT JOIN deltallm_usertable u
                ON u.user_id = v.user_id
            LEFT JOIN deltallm_serviceaccount s
                ON s.service_account_id = v.owner_service_account_id
            LEFT JOIN deltallm_teamtable t
                ON t.team_id = COALESCE(v.team_id, u.team_id, s.team_id)
            LEFT JOIN deltallm_organizationtable o
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
        organization_id = row.get("organization_id")
        lifecycle_state = row.get("organization_lifecycle_state")
        if not lifecycle_state:
            lifecycle_state = "missing" if organization_id else "active"

        return KeyRecord(
            token=row["token"],
            key_name=row.get("key_name"),
            user_id=row.get("user_id"),
            team_id=row.get("team_id"),
            owner_account_id=row.get("owner_account_id"),
            models=row.get("models") or [],
            team_models=row.get("team_models") or [],
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
            team_model_rpm_limit=_parse_metadata(row.get("team_model_rpm_limit")),
            team_model_tpm_limit=_parse_metadata(row.get("team_model_tpm_limit")),
            org_model_rpm_limit=_parse_metadata(row.get("org_model_rpm_limit")),
            org_model_tpm_limit=_parse_metadata(row.get("org_model_tpm_limit")),
            max_parallel_requests=row.get("max_parallel_requests"),
            key_rph_limit=row.get("key_rph_limit"),
            key_rpd_limit=row.get("key_rpd_limit"),
            key_tpd_limit=row.get("key_tpd_limit"),
            user_rph_limit=row.get("user_rph_limit"),
            user_rpd_limit=row.get("user_rpd_limit"),
            user_tpd_limit=row.get("user_tpd_limit"),
            team_rph_limit=row.get("team_rph_limit"),
            team_rpd_limit=row.get("team_rpd_limit"),
            team_tpd_limit=row.get("team_tpd_limit"),
            org_rph_limit=row.get("org_rph_limit"),
            org_rpd_limit=row.get("org_rpd_limit"),
            org_tpd_limit=row.get("org_tpd_limit"),
            organization_id=organization_id,
            organization_lifecycle_state=str(lifecycle_state),
            organization_lifecycle_version=int(row.get("organization_lifecycle_version") or 0),
            organization_lifecycle_generation=int(
                row.get("organization_lifecycle_generation") or 0
            ),
            guardrails=row.get("guardrails"),
            metadata=_parse_metadata(row.get("metadata")),
            team_metadata=_parse_metadata(row.get("team_metadata")),
            org_metadata=_parse_metadata(row.get("org_metadata")),
            expires=expires,
        )


__all__ = ["KeyRecord", "KeyRepository"]
