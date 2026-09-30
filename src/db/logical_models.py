from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


@dataclass(frozen=True, slots=True)
class LogicalModelRecord:
    model_id: str
    model_name: str
    managed_asset_id: str
    display_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LogicalModelRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    def with_db(self, prisma_client: Any) -> LogicalModelRepository:
        return LogicalModelRepository(prisma_client)

    async def get_by_name(self, model_name: str) -> LogicalModelRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT model_id, model_name, display_name, managed_asset_id, created_at, updated_at
            FROM deltallm_model
            WHERE model_name = $1
            LIMIT 1
            """,
            model_name,
        )
        return self._record_from_row(rows[0]) if rows else None

    async def get_by_deployment_id(self, deployment_id: str) -> LogicalModelRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT model.model_id, model.model_name, model.display_name, model.managed_asset_id,
                   model.created_at, model.updated_at
            FROM deltallm_modeldeployment AS deployment
            JOIN deltallm_model AS model ON model.model_id = deployment.model_id
            WHERE deployment.deployment_id = $1
            LIMIT 1
            """,
            deployment_id,
        )
        return self._record_from_row(rows[0]) if rows else None

    async def list_by_managed_asset_ids(
        self,
        managed_asset_ids: list[str],
    ) -> list[LogicalModelRecord]:
        if self.prisma is None or not managed_asset_ids:
            return []
        normalized_ids = [str(item).strip() for item in managed_asset_ids if str(item).strip()]
        if not normalized_ids:
            return []
        placeholders = ", ".join(f"${index}" for index in range(1, len(normalized_ids) + 1))
        rows = await self.prisma.query_raw(
            f"""
            SELECT model_id, model_name, display_name, managed_asset_id, created_at, updated_at
            FROM deltallm_model
            WHERE managed_asset_id IN ({placeholders})
            ORDER BY model_name ASC
            """,
            *normalized_ids,
        )
        return [self._record_from_row(row) for row in rows]

    async def list_by_names(self, model_names: list[str]) -> list[LogicalModelRecord]:
        if self.prisma is None or not model_names:
            return []
        normalized_names = list(
            dict.fromkeys(str(item).strip() for item in model_names if str(item).strip())
        )
        if not normalized_names:
            return []
        records: list[LogicalModelRecord] = []
        for start in range(0, len(normalized_names), 500):
            batch = normalized_names[start : start + 500]
            placeholders = ", ".join(f"${index}" for index in range(1, len(batch) + 1))
            rows = await self.prisma.query_raw(
                f"""
                SELECT model_id, model_name, display_name, managed_asset_id,
                       created_at, updated_at
                FROM deltallm_model
                WHERE model_name IN ({placeholders})
                """,
                *batch,
            )
            records.extend(self._record_from_row(row) for row in rows)
        return records

    async def create(self, record: LogicalModelRecord) -> LogicalModelRecord:
        if self.prisma is None:
            return record
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_model (
                model_id,
                model_name,
                display_name,
                managed_asset_id,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, NOW(), NOW())
            RETURNING model_id, model_name, display_name, managed_asset_id, created_at, updated_at
            """,
            record.model_id,
            record.model_name,
            record.display_name,
            record.managed_asset_id,
        )
        return self._record_from_row(rows[0])

    async def rename(self, model_id: str, model_name: str) -> LogicalModelRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_model
            SET model_name = $2,
                updated_at = NOW()
            WHERE model_id = $1
            RETURNING model_id, model_name, display_name, managed_asset_id, created_at, updated_at
            """,
            model_id,
            model_name,
        )
        return self._record_from_row(rows[0]) if rows else None

    async def update_display_name(
        self, model_id: str, display_name: str
    ) -> LogicalModelRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_model
            SET display_name = $2,
                updated_at = NOW()
            WHERE model_id = $1
            RETURNING model_id, model_name, display_name, managed_asset_id, created_at, updated_at
            """,
            model_id,
            display_name,
        )
        return self._record_from_row(rows[0]) if rows else None

    async def get_creator_namespace(self, account_id: str) -> tuple[str, str | None] | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT email, api_namespace
            FROM deltallm_platformaccount
            WHERE account_id = $1
            LIMIT 1
            """,
            account_id,
        )
        if not rows:
            return None
        row = rows[0]
        return str(row.get("email") or ""), (
            str(row.get("api_namespace")) if row.get("api_namespace") is not None else None
        )

    async def claim_creator_namespace(self, account_id: str, namespace: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            WITH namespace_lock AS (
                SELECT pg_advisory_xact_lock(hashtextextended(lower($2), 0))
            )
            UPDATE deltallm_platformaccount AS account
            SET api_namespace = lower($2),
                updated_at = NOW()
            FROM namespace_lock
            WHERE account.account_id = $1
              AND (account.api_namespace IS NULL OR lower(account.api_namespace) = lower($2))
              AND NOT EXISTS (
                  SELECT 1
                  FROM deltallm_platformaccount AS other
                  WHERE other.account_id <> $1
                    AND lower(other.api_namespace) = lower($2)
              )
            RETURNING account.account_id
            """,
            account_id,
            namespace,
        )
        return bool(rows)

    async def count_deployments(self, model_id: str) -> int:
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            SELECT COUNT(*)::int AS count
            FROM deltallm_modeldeployment
            WHERE model_id = $1
            """,
            model_id,
        )
        return int((rows[0] if rows else {}).get("count") or 0)

    async def delete(self, model_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_model
            WHERE model_id = $1
            RETURNING model_id
            """,
            model_id,
        )
        return bool(rows)

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> LogicalModelRecord:
        return LogicalModelRecord(
            model_id=str(row.get("model_id") or ""),
            model_name=str(row.get("model_name") or ""),
            managed_asset_id=str(row.get("managed_asset_id") or ""),
            display_name=(
                str(row.get("display_name"))
                if row.get("display_name") is not None
                else str(row.get("model_name") or "")
            ),
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
