from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


@dataclass
class NamedCredentialRecord:
    credential_id: str
    name: str
    provider: str
    connection_config: dict[str, Any]
    metadata: dict[str, Any] | None = None
    created_by_account_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NamedCredentialRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_all(self, *, provider: str | None = None) -> list[NamedCredentialRecord]:
        if self.prisma is None:
            return []

        params: list[Any] = []
        where_sql = ""
        if provider:
            params.append(str(provider).strip().lower())
            where_sql = f"WHERE LOWER(provider) = ${len(params)}"

        rows = await self.prisma.query_raw(
            f"""
            SELECT credential_id, name, provider, connection_config, metadata, created_by_account_id, created_at, updated_at
            FROM deltallm_namedcredential
            {where_sql}
            ORDER BY name ASC
            """,
            *params,
        )
        return [self._record_from_row(row) for row in rows]

    async def list_by_ids(self, credential_ids: list[str]) -> dict[str, NamedCredentialRecord]:
        if self.prisma is None or not credential_ids:
            return {}

        normalized_ids = [str(item).strip() for item in credential_ids if str(item).strip()]
        if not normalized_ids:
            return {}

        placeholders = ", ".join(f"${index}" for index in range(1, len(normalized_ids) + 1))
        rows = await self.prisma.query_raw(
            f"""
            SELECT credential_id, name, provider, connection_config, metadata, created_by_account_id, created_at, updated_at
            FROM deltallm_namedcredential
            WHERE credential_id IN ({placeholders})
            """,
            *normalized_ids,
        )
        return {record.credential_id: record for record in (self._record_from_row(row) for row in rows)}

    async def get_by_id(self, credential_id: str) -> NamedCredentialRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT credential_id, name, provider, connection_config, metadata, created_by_account_id, created_at, updated_at
            FROM deltallm_namedcredential
            WHERE credential_id = $1
            LIMIT 1
            """,
            credential_id,
        )
        if not rows:
            return None
        return self._record_from_row(rows[0])

    async def get_by_name(self, name: str) -> NamedCredentialRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT credential_id, name, provider, connection_config, metadata, created_by_account_id, created_at, updated_at
            FROM deltallm_namedcredential
            WHERE name = $1
            LIMIT 1
            """,
            name,
        )
        if not rows:
            return None
        return self._record_from_row(rows[0])

    async def create(self, record: NamedCredentialRecord) -> NamedCredentialRecord:
        if self.prisma is None:
            return record

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_namedcredential (
                credential_id,
                name,
                provider,
                connection_config,
                metadata,
                created_by_account_id,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, NOW(), NOW())
            RETURNING credential_id, name, provider, connection_config, metadata, created_by_account_id, created_at, updated_at
            """,
            record.credential_id,
            record.name,
            record.provider,
            json.dumps(record.connection_config),
            json.dumps(record.metadata) if record.metadata is not None else None,
            record.created_by_account_id,
        )
        return self._record_from_row(rows[0])

    async def update(
        self,
        credential_id: str,
        *,
        name: str,
        provider: str,
        connection_config: dict[str, Any],
        metadata: dict[str, Any] | None,
    ) -> NamedCredentialRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_namedcredential
            SET name = $2,
                provider = $3,
                connection_config = $4::jsonb,
                metadata = $5::jsonb,
                updated_at = NOW()
            WHERE credential_id = $1
            RETURNING credential_id, name, provider, connection_config, metadata, created_by_account_id, created_at, updated_at
            """,
            credential_id,
            name,
            provider,
            json.dumps(connection_config),
            json.dumps(metadata) if metadata is not None else None,
        )
        if not rows:
            return None
        return self._record_from_row(rows[0])

    async def delete(self, credential_id: str) -> bool:
        if self.prisma is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_namedcredential
            WHERE credential_id = $1
            RETURNING credential_id
            """,
            credential_id,
        )
        return bool(rows)

    async def count_linked_deployments(self, credential_id: str) -> int:
        if self.prisma is None:
            return 0

        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_modeldeployment
            WHERE named_credential_id = $1
            """,
            credential_id,
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def list_usage_counts(self) -> dict[str, int]:
        if self.prisma is None:
            return {}

        rows = await self.prisma.query_raw(
            """
            SELECT named_credential_id, COUNT(*)::int AS count
            FROM deltallm_modeldeployment
            WHERE named_credential_id IS NOT NULL
            GROUP BY named_credential_id
            """
        )
        return {
            str(row.get("named_credential_id") or ""): int(row.get("count") or 0)
            for row in rows
            if row.get("named_credential_id") is not None
        }

    async def list_linked_deployments(self, credential_id: str, *, limit: int = 25) -> list[dict[str, str]]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            SELECT deployment_id, model_name
            FROM deltallm_modeldeployment
            WHERE named_credential_id = $1
            ORDER BY model_name ASC
            LIMIT $2
            """,
            credential_id,
            limit,
        )
        return [
            {
                "deployment_id": str(row.get("deployment_id") or ""),
                "model_name": str(row.get("model_name") or ""),
            }
            for row in rows
        ]

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> NamedCredentialRecord:
        return NamedCredentialRecord(
            credential_id=str(row.get("credential_id") or ""),
            name=str(row.get("name") or ""),
            provider=str(row.get("provider") or ""),
            connection_config=_parse_json_object(row.get("connection_config")),
            metadata=_parse_json_object(row.get("metadata")) or None,
            created_by_account_id=str(row.get("created_by_account_id")) if row.get("created_by_account_id") is not None else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
