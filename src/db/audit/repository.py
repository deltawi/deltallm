"""Database records for audit events, payloads, and retention."""

from __future__ import annotations

import json

from dataclasses import dataclass

from datetime import UTC, datetime

from typing import Any

from uuid import uuid4


from src.db.json_fields import _parse_metadata


AUDIT_METADATA_RETENTION_DAYS_KEY = "audit_metadata_retention_days"


AUDIT_PAYLOAD_RETENTION_DAYS_KEY = "audit_payload_retention_days"


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

    def with_db(self, prisma_client: Any | None) -> AuditRepository:
        return AuditRepository(prisma_client)

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
                $1::uuid, $2, $3, $4, $5, $6,
                $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16,
                $17, $18, $19::jsonb, $20, $21, $22
            )
            ON CONFLICT (event_id) DO NOTHING
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
            VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6, $7, $8, $9)
            ON CONFLICT (payload_id) DO NOTHING
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

    async def create_events_batch(self, records: list[AuditEventRecord]) -> int:
        if self.prisma is None or not records:
            return 0
        payload = [
            {
                **record.__dict__,
                "event_id": record.event_id or str(uuid4()),
            }
            for record in records
        ]
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_auditevent (
                event_id, occurred_at, organization_id, actor_type, actor_id, api_key,
                action, resource_type, resource_id, request_id, correlation_id, ip,
                user_agent, status, latency_ms, input_tokens, output_tokens, error_type,
                error_code, metadata, content_stored, prev_hash, event_hash
            )
            SELECT
                (item->>'event_id')::uuid,
                COALESCE((item->>'occurred_at')::timestamptz, NOW()),
                item->>'organization_id', item->>'actor_type', item->>'actor_id',
                item->>'api_key', item->>'action', item->>'resource_type',
                item->>'resource_id', item->>'request_id', item->>'correlation_id',
                item->>'ip', item->>'user_agent', item->>'status',
                (item->>'latency_ms')::int, (item->>'input_tokens')::int,
                (item->>'output_tokens')::int, item->>'error_type', item->>'error_code',
                NULLIF(item->'metadata', 'null'::jsonb),
                COALESCE((item->>'content_stored')::boolean, FALSE),
                item->>'prev_hash', item->>'event_hash'
            FROM jsonb_array_elements($1::jsonb) AS source(item)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            json.dumps(payload, default=str),
        )
        return len(rows)

    async def create_payloads_batch(self, records: list[AuditPayloadRecord]) -> int:
        if self.prisma is None or not records:
            return 0
        payload = [
            {
                **record.__dict__,
                "payload_id": record.payload_id or str(uuid4()),
            }
            for record in records
        ]
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_auditpayload (
                payload_id, event_id, kind, storage_mode, content_json, storage_uri,
                content_sha256, size_bytes, redacted, created_at
            )
            SELECT
                (item->>'payload_id')::uuid, (item->>'event_id')::uuid,
                item->>'kind', COALESCE(item->>'storage_mode', 'inline'),
                NULLIF(item->'content_json', 'null'::jsonb), item->>'storage_uri',
                item->>'content_sha256', (item->>'size_bytes')::int,
                COALESCE((item->>'redacted')::boolean, FALSE),
                COALESCE((item->>'created_at')::timestamptz, NOW())
            FROM jsonb_array_elements($1::jsonb) AS source(item)
            ON CONFLICT (payload_id) DO NOTHING
            RETURNING payload_id
            """,
            json.dumps(payload, default=str),
        )
        return len(rows)

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

    async def list_expired_payload_ids(
        self, *, default_retention_days: int, limit: int
    ) -> list[str]:
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
