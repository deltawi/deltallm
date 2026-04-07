from __future__ import annotations

from datetime import datetime
from typing import Any

from src.batch.models import BatchFileRecord, BatchItemCreate, BatchItemRecord, BatchJobRecord
from src.batch.repositories import (
    BatchFileRepository,
    BatchItemRepository,
    BatchJobRepository,
    BatchMaintenanceRepository,
)


class BatchRepository:
    """Compatibility facade delegating batch persistence by concern."""

    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client
        self.files = BatchFileRepository(prisma_client)
        self.jobs = BatchJobRepository(prisma_client)
        self.items = BatchItemRepository(prisma_client)
        self.maintenance = BatchMaintenanceRepository(prisma_client)

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
        expires_at: datetime | None = None,
    ) -> BatchFileRecord | None:
        return await self.files.create_file(
            purpose=purpose,
            filename=filename,
            bytes_size=bytes_size,
            storage_backend=storage_backend,
            storage_key=storage_key,
            checksum=checksum,
            created_by_api_key=created_by_api_key,
            created_by_user_id=created_by_user_id,
            created_by_team_id=created_by_team_id,
            expires_at=expires_at,
        )

    async def get_file(self, file_id: str) -> BatchFileRecord | None:
        return await self.files.get_file(file_id)

    async def create_job(
        self,
        *,
        endpoint: str,
        input_file_id: str,
        model: str | None,
        metadata: dict[str, Any] | None,
        created_by_api_key: str | None,
        created_by_user_id: str | None,
        created_by_team_id: str | None,
        expires_at: datetime | None,
        execution_mode: str = "managed_internal",
    ) -> BatchJobRecord | None:
        return await self.jobs.create_job(
            endpoint=endpoint,
            input_file_id=input_file_id,
            model=model,
            metadata=metadata,
            created_by_api_key=created_by_api_key,
            created_by_user_id=created_by_user_id,
            created_by_team_id=created_by_team_id,
            expires_at=expires_at,
            execution_mode=execution_mode,
        )

    async def get_job(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.get_job(batch_id)

    async def list_jobs(
        self,
        *,
        limit: int = 20,
        after: datetime | None = None,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
    ) -> list[BatchJobRecord]:
        return await self.jobs.list_jobs(
            limit=limit,
            after=after,
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
        )

    async def set_job_queued(self, batch_id: str, total_items: int) -> BatchJobRecord | None:
        return await self.jobs.set_job_queued(batch_id, total_items)

    async def request_cancel(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.request_cancel(batch_id)

    async def create_items(self, batch_id: str, items: list[BatchItemCreate]) -> int:
        return await self.items.create_items(batch_id, items)

    async def claim_next_job(self, *, worker_id: str, lease_seconds: int = 30) -> BatchJobRecord | None:
        return await self.jobs.claim_next_job(worker_id=worker_id, lease_seconds=lease_seconds)

    async def claim_items(
        self,
        *,
        batch_id: str,
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = 60,
    ) -> list[BatchItemRecord]:
        return await self.items.claim_items(
            batch_id=batch_id,
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def mark_item_completed(
        self,
        *,
        item_id: str,
        worker_id: str | None,
        response_body: dict[str, Any],
        usage: dict[str, Any] | None,
        provider_cost: float,
        billed_cost: float,
    ) -> bool:
        return await self.items.mark_item_completed(
            item_id=item_id,
            worker_id=worker_id,
            response_body=response_body,
            usage=usage,
            provider_cost=provider_cost,
            billed_cost=billed_cost,
        )

    async def mark_item_failed(
        self,
        *,
        item_id: str,
        worker_id: str | None,
        error_body: dict[str, Any],
        last_error: str,
        retryable: bool,
        retry_delay_seconds: int = 0,
    ) -> bool:
        return await self.items.mark_item_failed(
            item_id=item_id,
            worker_id=worker_id,
            error_body=error_body,
            last_error=last_error,
            retryable=retryable,
            retry_delay_seconds=retry_delay_seconds,
        )

    async def refresh_job_progress(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.refresh_job_progress(batch_id)

    async def renew_job_lease(self, *, batch_id: str, worker_id: str, lease_seconds: int) -> bool:
        return await self.jobs.renew_job_lease(batch_id=batch_id, worker_id=worker_id, lease_seconds=lease_seconds)

    async def reschedule_finalization(
        self,
        *,
        batch_id: str,
        worker_id: str,
        retry_delay_seconds: int,
    ) -> bool:
        return await self.jobs.reschedule_finalization(
            batch_id=batch_id,
            worker_id=worker_id,
            retry_delay_seconds=retry_delay_seconds,
        )

    async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
        await self.jobs.release_job_lease(batch_id=batch_id, worker_id=worker_id)

    async def renew_item_lease(self, *, item_id: str, worker_id: str, lease_seconds: int) -> bool:
        return await self.items.renew_item_lease(item_id=item_id, worker_id=worker_id, lease_seconds=lease_seconds)

    async def mark_pending_items_cancelled(self, batch_id: str) -> None:
        await self.items.mark_pending_items_cancelled(batch_id)

    async def list_items(self, batch_id: str) -> list[BatchItemRecord]:
        return await self.items.list_items(batch_id)

    async def attach_artifacts_and_finalize(
        self,
        *,
        batch_id: str,
        output_file_id: str | None,
        error_file_id: str | None,
        final_status: str,
        worker_id: str | None = None,
    ) -> BatchJobRecord | None:
        return await self.jobs.attach_artifacts_and_finalize(
            batch_id=batch_id,
            output_file_id=output_file_id,
            error_file_id=error_file_id,
            final_status=final_status,
            worker_id=worker_id,
        )

    async def list_expired_terminal_job_ids(self, *, now: datetime, limit: int = 100) -> list[str]:
        return await self.maintenance.list_expired_terminal_job_ids(now=now, limit=limit)

    async def delete_job_metadata(self, batch_id: str) -> None:
        await self.maintenance.delete_job_metadata(batch_id)

    async def list_expired_unreferenced_files(self, *, now: datetime, limit: int = 100) -> list[BatchFileRecord]:
        return await self.files.list_expired_unreferenced_files(now=now, limit=limit)

    async def delete_file(self, file_id: str) -> None:
        await self.files.delete_file(file_id)
