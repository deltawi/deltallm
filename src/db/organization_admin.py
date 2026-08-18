from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Protocol

from src.db.audit_ingestion import AuditIngestionRepository


class OrganizationAdminDatabase(Protocol):
    async def execute_raw(self, query: str, *params: object) -> int: ...

    async def query_raw(
        self,
        query: str,
        *params: object,
    ) -> list[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class OrganizationPersistenceValues:
    organization_id: str
    organization_name: str | None
    max_budget: float | None
    soft_budget: float | None
    budget_duration: str | None
    budget_reset_at: datetime | None
    rpm_limit: int | None
    tpm_limit: int | None
    rph_limit: int | None
    rpd_limit: int | None
    tpd_limit: int | None
    model_rpm_limit: dict[str, int] | None
    model_tpm_limit: dict[str, int] | None
    audit_content_storage_enabled: bool
    metadata: dict[str, object] | None


class OrganizationAdminRepository:
    def __init__(self, db: OrganizationAdminDatabase) -> None:
        self.db = db

    async def upsert(
        self,
        values: OrganizationPersistenceValues,
        *,
        reset_fields_provided: bool,
    ) -> dict[str, object] | None:
        await self.db.execute_raw(
            """
            WITH policy_lock AS MATERIALIZED (
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-content-policy:' || $1, 0)
                )
            )
            INSERT INTO deltallm_organizationtable (
                id,
                organization_id,
                organization_name,
                max_budget,
                soft_budget,
                budget_duration,
                budget_reset_at,
                spend,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                model_rpm_limit,
                model_tpm_limit,
                audit_content_storage_enabled,
                metadata,
                created_at,
                updated_at
            )
            SELECT gen_random_uuid(), $1, $2, $3, $4, $5, $6::timestamp, 0, $7, $8, $9, $10, $11, $12::jsonb, $13::jsonb, $14, $15::jsonb, NOW(), NOW()
            FROM policy_lock
            ON CONFLICT (organization_id)
            DO UPDATE SET
                organization_name = EXCLUDED.organization_name,
                max_budget = EXCLUDED.max_budget,
                soft_budget = EXCLUDED.soft_budget,
                budget_duration = CASE
                    WHEN $16::boolean THEN EXCLUDED.budget_duration
                    ELSE deltallm_organizationtable.budget_duration
                END,
                budget_reset_at = CASE
                    WHEN $16::boolean THEN EXCLUDED.budget_reset_at
                    ELSE deltallm_organizationtable.budget_reset_at
                END,
                rpm_limit = EXCLUDED.rpm_limit,
                tpm_limit = EXCLUDED.tpm_limit,
                rph_limit = EXCLUDED.rph_limit,
                rpd_limit = EXCLUDED.rpd_limit,
                tpd_limit = EXCLUDED.tpd_limit,
                model_rpm_limit = EXCLUDED.model_rpm_limit,
                model_tpm_limit = EXCLUDED.model_tpm_limit,
                audit_content_storage_enabled = EXCLUDED.audit_content_storage_enabled,
                audit_content_policy_version = CASE
                    WHEN deltallm_organizationtable.audit_content_storage_enabled
                         IS DISTINCT FROM EXCLUDED.audit_content_storage_enabled
                    THEN deltallm_organizationtable.audit_content_policy_version + 1
                    ELSE deltallm_organizationtable.audit_content_policy_version
                END,
                metadata = CASE
                    WHEN NOT $16::boolean
                    THEN NULLIF(COALESCE(deltallm_organizationtable.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb), '{}'::jsonb)
                    WHEN EXCLUDED.budget_duration IS NULL OR EXCLUDED.budget_duration NOT LIKE '%mo'
                    THEN NULLIF((COALESCE(deltallm_organizationtable.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb)) - '_budget_reset', '{}'::jsonb)
                    ELSE NULLIF(COALESCE(deltallm_organizationtable.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb), '{}'::jsonb)
                END,
                updated_at = NOW()
            """,
            values.organization_id,
            values.organization_name,
            values.max_budget,
            values.soft_budget,
            values.budget_duration,
            values.budget_reset_at,
            values.rpm_limit,
            values.tpm_limit,
            values.rph_limit,
            values.rpd_limit,
            values.tpd_limit,
            _json_or_none(values.model_rpm_limit),
            _json_or_none(values.model_tpm_limit),
            values.audit_content_storage_enabled,
            values.metadata,
            reset_fields_provided,
        )
        await self._redact_if_disabled(values)
        return await self.get(values.organization_id)

    async def update(
        self,
        values: OrganizationPersistenceValues,
    ) -> dict[str, object] | None:
        await self.db.execute_raw(
            """
            WITH policy_lock AS MATERIALIZED (
                SELECT pg_advisory_xact_lock(
                    hashtextextended('deltallm:audit-content-policy:' || $15, 0)
                )
            )
            UPDATE deltallm_organizationtable
            SET organization_name = $1,
                max_budget = $2,
                soft_budget = $3,
                budget_duration = $4,
                budget_reset_at = $5::timestamp,
                rpm_limit = $6,
                tpm_limit = $7,
                rph_limit = $8,
                rpd_limit = $9,
                tpd_limit = $10,
                model_rpm_limit = $11::jsonb,
                model_tpm_limit = $12::jsonb,
                audit_content_storage_enabled = $13,
                audit_content_policy_version = CASE
                    WHEN audit_content_storage_enabled IS DISTINCT FROM $13
                    THEN audit_content_policy_version + 1
                    ELSE audit_content_policy_version
                END,
                metadata = $14::jsonb,
                updated_at = NOW()
            FROM policy_lock
            WHERE organization_id = $15
            """,
            values.organization_name,
            values.max_budget,
            values.soft_budget,
            values.budget_duration,
            values.budget_reset_at,
            values.rpm_limit,
            values.tpm_limit,
            values.rph_limit,
            values.rpd_limit,
            values.tpd_limit,
            _json_or_none(values.model_rpm_limit),
            _json_or_none(values.model_tpm_limit),
            values.audit_content_storage_enabled,
            values.metadata,
            values.organization_id,
        )
        await self._redact_if_disabled(values)
        return await self.get(values.organization_id)

    async def get(self, organization_id: str) -> dict[str, object] | None:
        rows = await self.db.query_raw(
            """
            SELECT organization_id, organization_name, max_budget, soft_budget, spend, budget_duration, budget_reset_at, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, model_rpm_limit, model_tpm_limit, audit_content_storage_enabled, metadata, created_at, updated_at
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        return dict(rows[0]) if rows else None

    async def _redact_if_disabled(self, values: OrganizationPersistenceValues) -> None:
        if values.audit_content_storage_enabled:
            return
        await AuditIngestionRepository(self.db).redact_active_for_current_policy(
            values.organization_id
        )


def _json_or_none(value: dict[str, int] | None) -> str | None:
    return json.dumps(value) if value else None
