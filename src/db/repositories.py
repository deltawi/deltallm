from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

AUDIT_METADATA_RETENTION_DAYS_KEY = "audit_metadata_retention_days"
AUDIT_PAYLOAD_RETENTION_DAYS_KEY = "audit_payload_retention_days"


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


@dataclass
class AuditEventRecord:
    event_id: str
    action: str
    occurred_at: datetime | None = None
    organization_id: str | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    api_key: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    metadata: dict[str, Any] | None = None
    content_stored: bool = False
    prev_hash: str | None = None
    event_hash: str | None = None


@dataclass
class AuditPayloadRecord:
    payload_id: str
    event_id: str
    kind: str
    storage_mode: str = "inline"
    content_json: dict[str, Any] | None = None
    storage_uri: str | None = None
    content_sha256: str | None = None
    size_bytes: int | None = None
    redacted: bool = False
    created_at: datetime | None = None


class AuditRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def create_event(self, record: AuditEventRecord) -> AuditEventRecord:
        if self.prisma is None:
            return record

        event_id = record.event_id.strip() if record.event_id else ""
        if not event_id:
            event_id = str(uuid4())

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_auditevent (
                event_id, organization_id, actor_type, actor_id, api_key, action,
                resource_type, resource_id, request_id, correlation_id,
                ip, user_agent, status, latency_ms, input_tokens, output_tokens,
                error_type, error_code, metadata, content_stored, prev_hash, event_hash
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16,
                $17, $18, $19::jsonb, $20, $21, $22
            )
            RETURNING
                event_id, occurred_at, organization_id, actor_type, actor_id, api_key, action,
                resource_type, resource_id, request_id, correlation_id, ip, user_agent, status,
                latency_ms, input_tokens, output_tokens, error_type, error_code, metadata,
                content_stored, prev_hash, event_hash
            """,
            event_id,
            record.organization_id,
            record.actor_type,
            record.actor_id,
            record.api_key,
            record.action,
            record.resource_type,
            record.resource_id,
            record.request_id,
            record.correlation_id,
            record.ip,
            record.user_agent,
            record.status,
            record.latency_ms,
            record.input_tokens,
            record.output_tokens,
            record.error_type,
            record.error_code,
            json.dumps(record.metadata) if record.metadata is not None else None,
            record.content_stored,
            record.prev_hash,
            record.event_hash,
        )
        if not rows:
            return record
        return self._to_event_record(rows[0])

    async def create_payload(self, record: AuditPayloadRecord) -> AuditPayloadRecord:
        if self.prisma is None:
            return record

        payload_id = record.payload_id.strip() if record.payload_id else ""
        if not payload_id:
            payload_id = str(uuid4())

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_auditpayload (
                payload_id, event_id, kind, storage_mode, content_json, storage_uri,
                content_sha256, size_bytes, redacted
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            RETURNING payload_id, event_id, kind, storage_mode, content_json, storage_uri,
                      content_sha256, size_bytes, redacted, created_at
            """,
            payload_id,
            record.event_id,
            record.kind,
            record.storage_mode,
            json.dumps(record.content_json) if record.content_json is not None else None,
            record.storage_uri,
            record.content_sha256,
            record.size_bytes,
            record.redacted,
        )
        if not rows:
            return record
        return self._to_payload_record(rows[0])

    async def is_content_storage_enabled_for_org(self, organization_id: str | None) -> bool:
        if self.prisma is None or not organization_id:
            return False
        rows = await self.prisma.query_raw(
            """
            SELECT audit_content_storage_enabled
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            LIMIT 1
            """,
            organization_id,
        )
        if not rows:
            return False
        return bool(rows[0].get("audit_content_storage_enabled", False))

    async def list_expired_event_ids(self, *, default_retention_days: int, limit: int) -> list[str]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            f"""
            SELECT e.event_id
            FROM deltallm_auditevent e
            LEFT JOIN deltallm_organizationtable o ON o.organization_id = e.organization_id
            WHERE e.occurred_at < NOW() - make_interval(days => GREATEST(
                COALESCE((o.metadata->>'{AUDIT_METADATA_RETENTION_DAYS_KEY}')::int, $1::int),
                1
            )::int)
            ORDER BY e.occurred_at ASC
            LIMIT $2
            """,
            max(1, int(default_retention_days)),
            max(1, int(limit)),
        )
        return [str(row.get("event_id")) for row in rows if row.get("event_id")]

    async def list_expired_payload_ids(self, *, default_retention_days: int, limit: int) -> list[str]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            f"""
            SELECT p.payload_id
            FROM deltallm_auditpayload p
            JOIN deltallm_auditevent e ON e.event_id = p.event_id
            LEFT JOIN deltallm_organizationtable o ON o.organization_id = e.organization_id
            WHERE p.created_at < NOW() - make_interval(days => GREATEST(
                COALESCE((o.metadata->>'{AUDIT_PAYLOAD_RETENTION_DAYS_KEY}')::int, $1::int),
                1
            )::int)
            ORDER BY p.created_at ASC
            LIMIT $2
            """,
            max(1, int(default_retention_days)),
            max(1, int(limit)),
        )
        return [str(row.get("payload_id")) for row in rows if row.get("payload_id")]

    async def delete_payloads_by_ids(self, payload_ids: list[str]) -> int:
        if self.prisma is None or not payload_ids:
            return 0
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_auditpayload
            WHERE payload_id = ANY($1::uuid[])
            RETURNING payload_id
            """,
            payload_ids,
        )
        return len(rows)

    async def delete_events_by_ids(self, event_ids: list[str]) -> int:
        if self.prisma is None or not event_ids:
            return 0
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_auditevent
            WHERE event_id = ANY($1::uuid[])
            RETURNING event_id
            """,
            event_ids,
        )
        return len(rows)

    @staticmethod
    def _to_event_record(row: dict[str, Any]) -> AuditEventRecord:
        occurred_at = row.get("occurred_at")
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00")).astimezone(UTC)
        return AuditEventRecord(
            event_id=str(row.get("event_id") or ""),
            action=str(row.get("action") or ""),
            occurred_at=occurred_at if isinstance(occurred_at, datetime) else None,
            organization_id=row.get("organization_id"),
            actor_type=row.get("actor_type"),
            actor_id=row.get("actor_id"),
            api_key=row.get("api_key"),
            resource_type=row.get("resource_type"),
            resource_id=row.get("resource_id"),
            request_id=row.get("request_id"),
            correlation_id=row.get("correlation_id"),
            ip=row.get("ip"),
            user_agent=row.get("user_agent"),
            status=row.get("status"),
            latency_ms=row.get("latency_ms"),
            input_tokens=row.get("input_tokens"),
            output_tokens=row.get("output_tokens"),
            error_type=row.get("error_type"),
            error_code=row.get("error_code"),
            metadata=_parse_metadata(row.get("metadata")),
            content_stored=bool(row.get("content_stored", False)),
            prev_hash=row.get("prev_hash"),
            event_hash=row.get("event_hash"),
        )

    @staticmethod
    def _to_payload_record(row: dict[str, Any]) -> AuditPayloadRecord:
        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(UTC)
        return AuditPayloadRecord(
            payload_id=str(row.get("payload_id") or ""),
            event_id=str(row.get("event_id") or ""),
            kind=str(row.get("kind") or ""),
            storage_mode=str(row.get("storage_mode") or "inline"),
            content_json=_parse_metadata(row.get("content_json")),
            storage_uri=row.get("storage_uri"),
            content_sha256=row.get("content_sha256"),
            size_bytes=row.get("size_bytes"),
            redacted=bool(row.get("redacted", False)),
            created_at=created_at if isinstance(created_at, datetime) else None,
        )
