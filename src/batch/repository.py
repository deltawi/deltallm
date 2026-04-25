from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.models import (
    BatchCompletionOutboxCreate,
    BatchCompletionOutboxRecord,
    BatchCompletionOutboxStatus,
    BatchFileRecord,
    BatchItemCreate,
    BatchItemRecord,
    BatchJobRecord,
)
from src.batch.repositories import (
    BatchCompletionOutboxRepository,
    BatchFileRepository,
    BatchItemRepository,
    BatchJobRepository,
    BatchMaintenanceRepository,
)


class BatchRepository:
    """Compatibility facade delegating batch persistence by concern."""

    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client
        self.create_sessions = BatchCreateSessionRepository(prisma_client)
        self.files = BatchFileRepository(prisma_client)
        self.jobs = BatchJobRepository(prisma_client)
        self.items = BatchItemRepository(prisma_client)
        self.completion_outbox = BatchCompletionOutboxRepository(prisma_client)
        self.maintenance = BatchMaintenanceRepository(prisma_client)

    def with_prisma(self, prisma_client: Any | None) -> BatchRepository:
        return BatchRepository(prisma_client)

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
            created_by_organization_id=created_by_organization_id,
            expires_at=expires_at,
        )

    async def get_file(self, file_id: str) -> BatchFileRecord | None:
        return await self.files.get_file(file_id)

    async def create_job(
        self,
        *,
        batch_id: str | None = None,
        endpoint: str,
        input_file_id: str,
        model: str | None,
        metadata: dict[str, Any] | None,
        created_by_api_key: str | None,
        created_by_user_id: str | None,
        created_by_team_id: str | None,
        created_by_organization_id: str | None = None,
        expires_at: datetime | None = None,
        execution_mode: str = "managed_internal",
        status: str = "queued",
        total_items: int = 0,
    ) -> BatchJobRecord | None:
        return await self.jobs.create_job(
            batch_id=batch_id,
            endpoint=endpoint,
            input_file_id=input_file_id,
            model=model,
            metadata=metadata,
            created_by_api_key=created_by_api_key,
            created_by_user_id=created_by_user_id,
            created_by_team_id=created_by_team_id,
            created_by_organization_id=created_by_organization_id,
            expires_at=expires_at,
            execution_mode=execution_mode,
            status=status,
            total_items=total_items,
        )

    async def get_job(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.get_job(batch_id)

    async def acquire_scope_advisory_lock(self, *, scope_type: str, scope_id: str) -> None:
        await self.jobs.acquire_scope_advisory_lock(scope_type=scope_type, scope_id=scope_id)

    async def list_jobs(
        self,
        *,
        limit: int = 20,
        after: datetime | None = None,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
        created_by_organization_id: str | None = None,
    ) -> list[BatchJobRecord]:
        return await self.jobs.list_jobs(
            limit=limit,
            after=after,
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
            created_by_organization_id=created_by_organization_id,
        )

    async def count_active_jobs_for_scope(
        self,
        *,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
    ) -> int:
        return await self.jobs.count_active_jobs_for_scope(
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
        )

    async def summarize_runtime_statuses(self, *, now: datetime) -> dict[str, float]:
        job_summary = await self.jobs.summarize_runtime_statuses()
        item_summary = await self.items.summarize_runtime_statuses(now=now)
        return {
            **job_summary,
            **item_summary,
        }

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

    async def list_items_by_ids(self, item_ids: list[str]) -> list[BatchItemRecord]:
        return await self.items.list_items_by_ids(item_ids)

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

    async def mark_items_completed_bulk(
        self,
        *,
        items: list[dict[str, Any]],
        worker_id: str | None,
    ) -> bool:
        return await self.items.mark_items_completed_bulk(
            items=items,
            worker_id=worker_id,
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

    async def release_items_for_retry(
        self,
        *,
        item_ids: list[str],
        worker_id: str,
        retry_delay_seconds: int = 0,
        error_body: dict[str, Any] | None = None,
        last_error: str | None = None,
    ) -> list[str]:
        return await self.items.release_items_for_retry(
            item_ids=item_ids,
            worker_id=worker_id,
            retry_delay_seconds=retry_delay_seconds,
            error_body=error_body,
            last_error=last_error,
        )

    async def enqueue_completion_outbox_many(self, records: list[BatchCompletionOutboxCreate]) -> list[str]:
        return await self.completion_outbox.enqueue_many(records)

    async def claim_completion_outbox_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 25,
    ) -> list[BatchCompletionOutboxRecord]:
        return await self.completion_outbox.claim_due(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            limit=limit,
        )

    async def mark_completion_outbox_sent(self, completion_id: str, *, worker_id: str) -> bool:
        return await self.completion_outbox.mark_sent(completion_id, worker_id=worker_id)

    async def mark_completion_outbox_retry(
        self,
        completion_id: str,
        *,
        worker_id: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        return await self.completion_outbox.mark_retry(
            completion_id,
            worker_id=worker_id,
            error=error,
            next_attempt_at=next_attempt_at,
        )

    async def mark_completion_outbox_failed(self, completion_id: str, *, worker_id: str, error: str) -> bool:
        return await self.completion_outbox.mark_failed(completion_id, worker_id=worker_id, error=error)

    async def renew_completion_outbox_lease(self, *, completion_id: str, worker_id: str, lease_seconds: int) -> bool:
        return await self.completion_outbox.renew_lease(
            completion_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    async def list_completion_outbox_by_item_ids(self, item_ids: list[str]) -> list[BatchCompletionOutboxRecord]:
        return await self.completion_outbox.list_by_item_ids(item_ids)

    async def count_pending_completion_outbox(self) -> int:
        return await self.completion_outbox.count_pending()

    async def mark_pending_items_cancelled(self, batch_id: str) -> None:
        await self.items.mark_pending_items_cancelled(batch_id)

    async def list_items(self, batch_id: str) -> list[BatchItemRecord]:
        return await self.items.list_items(batch_id)

    async def list_items_page(
        self,
        *,
        batch_id: str,
        limit: int = 500,
        after_line_number: int | None = None,
    ) -> list[BatchItemRecord]:
        return await self.items.list_items_page(
            batch_id=batch_id,
            limit=limit,
            after_line_number=after_line_number,
        )

    async def requeue_expired_in_progress_items(self, batch_id: str) -> int:
        return await self.items.requeue_expired_in_progress_items(batch_id)

    async def fail_nonterminal_items(self, *, batch_id: str, reason: str) -> int:
        return await self.items.fail_nonterminal_items(batch_id=batch_id, reason=reason)

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

    async def retry_finalization_now(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.retry_finalization_now(batch_id)

    async def complete_item_with_outbox(
        self,
        *,
        item_id: str,
        worker_id: str | None,
        response_body: dict[str, Any],
        usage: dict[str, Any] | None,
        provider_cost: float,
        billed_cost: float,
        outbox_payload: dict[str, Any],
        outbox_max_attempts: int = 5,
    ) -> Literal["completed", "already_completed", "not_owned"]:
        return await self.complete_items_with_outbox_bulk(
            items=[
                {
                    "item_id": item_id,
                    "response_body": response_body,
                    "usage": usage,
                    "provider_cost": provider_cost,
                    "billed_cost": billed_cost,
                    "outbox_payload": outbox_payload,
                    "outbox_max_attempts": outbox_max_attempts,
                }
            ],
            worker_id=worker_id,
        )

    async def complete_items_with_outbox_bulk(
        self,
        *,
        items: list[dict[str, Any]],
        worker_id: str | None,
    ) -> Literal["completed", "already_completed", "not_owned"]:
        if not items:
            return "completed"

        async def _run_in_current_repo(repo: BatchRepository) -> Literal["completed", "already_completed", "not_owned"]:
            updated = await repo.mark_items_completed_bulk(
                items=[
                    {
                        "item_id": item["item_id"],
                        "response_body": item["response_body"],
                        "usage": item.get("usage"),
                        "provider_cost": item["provider_cost"],
                        "billed_cost": item["billed_cost"],
                    }
                    for item in items
                ],
                worker_id=worker_id,
            )
            if updated:
                completion_ids = await repo.enqueue_completion_outbox_many(
                    [
                        BatchCompletionOutboxCreate(
                            batch_id=str(item["outbox_payload"]["batch_id"]),
                            item_id=str(item["item_id"]),
                            payload_json=dict(item["outbox_payload"]),
                            status=BatchCompletionOutboxStatus.QUEUED,
                            max_attempts=int(item.get("outbox_max_attempts") or 5),
                        )
                        for item in items
                    ]
                )
                if len(completion_ids) != len(items):
                    raise RuntimeError("failed to enqueue one or more batch completion outbox rows")
                return "completed"

            item_ids = [str(item["item_id"]) for item in items]
            existing_items = await repo.list_items_by_ids(item_ids)
            existing_outbox = await repo.list_completion_outbox_by_item_ids(item_ids)
            completed_item_ids = {item.item_id for item in existing_items if item.status == "completed"}
            outbox_item_ids = {record.item_id for record in existing_outbox}
            if completed_item_ids == set(item_ids) and outbox_item_ids == set(item_ids):
                return "already_completed"
            return "not_owned"

        if self.prisma is not None and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await _run_in_current_repo(self.with_prisma(tx))
        return await _run_in_current_repo(self)

    async def set_provider_error(self, *, batch_id: str, provider_error: str | None) -> BatchJobRecord | None:
        return await self.jobs.set_provider_error(batch_id=batch_id, provider_error=provider_error)

    async def list_expired_terminal_job_ids(self, *, now: datetime, limit: int = 100) -> list[str]:
        return await self.maintenance.list_expired_terminal_job_ids(now=now, limit=limit)

    async def delete_job_metadata(self, batch_id: str) -> None:
        await self.maintenance.delete_job_metadata(batch_id)

    async def list_expired_unreferenced_files(self, *, now: datetime, limit: int = 100) -> list[BatchFileRecord]:
        return await self.files.list_expired_unreferenced_files(now=now, limit=limit)

    async def delete_file(self, file_id: str) -> None:
        await self.files.delete_file(file_id)
