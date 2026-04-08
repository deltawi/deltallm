from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from src.batch.models import BatchFileRecord
from src.batch.repositories.mappers import file_from_row


class BatchFileRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def create_file(
        self,
        *,
        purpose: str,
        filename: str,
        bytes_size: int,
        storage_backend: str,
        storage_key: str,
        checksum: str | None = None,
        created_by_api_key: str | None = None,
        created_by_user_id: str | None = None,
        created_by_team_id: str | None = None,
        created_by_organization_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> BatchFileRecord | None:
        if self.prisma is None:
            return None
        file_id = str(uuid4())
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_batch_file (
                file_id, purpose, filename, bytes, status, storage_backend, storage_key, checksum,
                created_by_api_key, created_by_user_id, created_by_team_id, created_by_organization_id, expires_at
            )
            VALUES ($1, $2, $3, $4, 'processed', $5, $6, $7, $8, $9, $10, $11, $12::timestamp)
            RETURNING *
            """,
            file_id,
            purpose,
            filename,
            bytes_size,
            storage_backend,
            storage_key,
            checksum,
            created_by_api_key,
            created_by_user_id,
            created_by_team_id,
            created_by_organization_id,
            expires_at,
        )
        if not rows:
            return None
        return file_from_row(rows[0])

    async def get_file(self, file_id: str) -> BatchFileRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT *
            FROM deltallm_batch_file
            WHERE file_id = $1
            LIMIT 1
            """,
            file_id,
        )
        if not rows:
            return None
        return file_from_row(rows[0])

    async def list_expired_unreferenced_files(self, *, now: datetime, limit: int = 100) -> list[BatchFileRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            """
            SELECT f.*
            FROM deltallm_batch_file f
            WHERE f.expires_at IS NOT NULL
              AND f.expires_at < $1::timestamp
              AND NOT EXISTS (
                    SELECT 1
                    FROM deltallm_batch_job j
                    WHERE j.input_file_id = f.file_id
                       OR j.output_file_id = f.file_id
                       OR j.error_file_id = f.file_id
              )
            ORDER BY f.expires_at ASC
            LIMIT $2
            """,
            now,
            max(1, min(limit, 1000)),
        )
        return [file_from_row(row) for row in rows]

    async def delete_file(self, file_id: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_batch_file
            WHERE file_id = $1
            """,
            file_id,
        )
