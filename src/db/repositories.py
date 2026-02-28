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


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


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
    team_metadata: dict[str, Any] | None = None
    org_metadata: dict[str, Any] | None = None
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
                t.metadata AS team_metadata,
                o.metadata AS org_metadata,
                v.expires
            FROM deltallm_verificationtoken v
            LEFT JOIN deltallm_usertable u
                ON u.user_id = v.user_id
            LEFT JOIN deltallm_teamtable t
                ON t.team_id = COALESCE(v.team_id, u.team_id)
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
            metadata=_parse_metadata(row.get("metadata")),
            team_metadata=_parse_metadata(row.get("team_metadata")),
            org_metadata=_parse_metadata(row.get("org_metadata")),
            expires=expires,
        )


@dataclass
class ModelDeploymentRecord:
    deployment_id: str
    model_name: str
    deltallm_params: dict[str, Any]
    model_info: dict[str, Any] | None = None


class ModelDeploymentRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_all(self) -> list[ModelDeploymentRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            SELECT deployment_id, model_name, deltallm_params, model_info
            FROM deltallm_modeldeployment
            ORDER BY model_name ASC, created_at ASC
            """
        )
        return [
            ModelDeploymentRecord(
                deployment_id=str(row.get("deployment_id") or ""),
                model_name=str(row.get("model_name") or ""),
                deltallm_params=_parse_json_object(row.get("deltallm_params")),
                model_info=_parse_metadata(row.get("model_info")),
            )
            for row in rows
        ]

    async def get_by_deployment_id(self, deployment_id: str) -> ModelDeploymentRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT deployment_id, model_name, deltallm_params, model_info
            FROM deltallm_modeldeployment
            WHERE deployment_id = $1
            LIMIT 1
            """,
            deployment_id,
        )
        if not rows:
            return None
        row = rows[0]
        return ModelDeploymentRecord(
            deployment_id=str(row.get("deployment_id") or ""),
            model_name=str(row.get("model_name") or ""),
            deltallm_params=_parse_json_object(row.get("deltallm_params")),
            model_info=_parse_metadata(row.get("model_info")),
        )

    async def create(self, record: ModelDeploymentRecord) -> ModelDeploymentRecord:
        if self.prisma is None:
            return record

        await self.prisma.execute_raw(
            """
            INSERT INTO deltallm_modeldeployment (deployment_id, model_name, deltallm_params, model_info, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, NOW(), NOW())
            """,
            record.deployment_id,
            record.model_name,
            json.dumps(record.deltallm_params),
            json.dumps(record.model_info) if record.model_info is not None else None,
        )
        return record

    async def update(
        self,
        deployment_id: str,
        *,
        model_name: str,
        deltallm_params: dict[str, Any],
        model_info: dict[str, Any] | None,
    ) -> ModelDeploymentRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_modeldeployment
            SET model_name = $2,
                deltallm_params = $3::jsonb,
                model_info = $4::jsonb,
                updated_at = NOW()
            WHERE deployment_id = $1
            RETURNING deployment_id, model_name, deltallm_params, model_info
            """,
            deployment_id,
            model_name,
            json.dumps(deltallm_params),
            json.dumps(model_info) if model_info is not None else None,
        )
        if not rows:
            return None
        row = rows[0]
        return ModelDeploymentRecord(
            deployment_id=str(row.get("deployment_id") or ""),
            model_name=str(row.get("model_name") or ""),
            deltallm_params=_parse_json_object(row.get("deltallm_params")),
            model_info=_parse_metadata(row.get("model_info")),
        )

    async def delete(self, deployment_id: str) -> bool:
        if self.prisma is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_modeldeployment
            WHERE deployment_id = $1
            RETURNING deployment_id
            """,
            deployment_id,
        )
        return bool(rows)

    async def bulk_insert_if_empty(self, records: list[ModelDeploymentRecord]) -> bool:
        if self.prisma is None or not records:
            return False

        count_rows = await self.prisma.query_raw("SELECT COUNT(*)::int AS count FROM deltallm_modeldeployment")
        if count_rows and int(count_rows[0].get("count") or 0) > 0:
            return False

        for record in records:
            await self.prisma.execute_raw(
                """
                INSERT INTO deltallm_modeldeployment (deployment_id, model_name, deltallm_params, model_info, created_at, updated_at)
                VALUES ($1, $2, $3::jsonb, $4::jsonb, NOW(), NOW())
                ON CONFLICT (deployment_id) DO NOTHING
                """,
                record.deployment_id,
                record.model_name,
                json.dumps(record.deltallm_params),
                json.dumps(record.model_info) if record.model_info is not None else None,
            )
        return True
