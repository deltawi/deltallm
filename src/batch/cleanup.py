from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.batch.models import BatchWebhookOwnershipConflictError
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.metrics import (
    increment_batch_artifact_failure,
    publish_batch_runtime_summary,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchCleanupConfig:
    interval_seconds: float = 86_400.0
    failure_interval_seconds: float = 60.0
    scan_limit: int = 200
    webhook_delivery_retention_days: int = 30
    webhook_cleanup_max_rows_per_run: int = 10_000


class BatchRetentionCleanupWorker:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        storage: BatchArtifactStorage,
        storage_registry: dict[str, BatchArtifactStorage] | None = None,
        config: BatchCleanupConfig,
    ) -> None:
        self.repository = repository
        self.storage = storage
        active_backend = str(getattr(storage, "backend_name", "local") or "local").strip().lower()
        self.storage_registry = {
            str(key).strip().lower(): value
            for key, value in (storage_registry or {}).items()
        }
        self.storage_registry.setdefault(active_backend, storage)
        self.config = config
        self._running = False
        self._stop_event = asyncio.Event()

    def _storage_for_backend(self, backend: str | None) -> BatchArtifactStorage:
        normalized = str(backend or getattr(self.storage, "backend_name", "local") or "local").strip().lower()
        storage = self.storage_registry.get(normalized)
        if storage is None:
            raise RuntimeError(
                f"Storage backend '{normalized}' is unavailable; keep legacy batch storage configured until referenced files expire"
            )
        return storage

    async def _refresh_batch_runtime_metrics(self, *, now: datetime) -> None:
        try:
            summary = await self.repository.summarize_runtime_statuses(now=now)
            publish_batch_runtime_summary(summary)
        except Exception:
            logger.debug("batch cleanup runtime metrics refresh failed", exc_info=True)
            return

    async def _cleanup_webhook_deliveries(self, *, now: datetime) -> dict[str, int]:
        cutoff = now - timedelta(
            days=max(1, int(self.config.webhook_delivery_retention_days))
        )
        deleted = {"delivered": 0, "failed": 0}
        remaining = max(1, int(self.config.webhook_cleanup_max_rows_per_run))
        page_size = max(1, min(int(self.config.scan_limit), 1_000))
        while remaining > 0:
            page_limit = min(page_size, remaining)
            try:
                page = await self.repository.delete_terminal_webhook_outbox_before(
                    cutoff=cutoff,
                    limit=page_limit,
                )
            except Exception:
                logger.warning(
                    "batch webhook cleanup failed",
                    extra={"reason": "repository_error"},
                )
                break
            page_deleted = sum(max(0, int(value)) for value in page.values())
            deleted["delivered"] += max(0, int(page.get("delivered", 0)))
            deleted["failed"] += max(0, int(page.get("failed", 0)))
            remaining -= min(page_deleted, remaining)
            if page_deleted <= 0 or page_deleted < page_limit:
                break
        if remaining == 0:
            logger.info(
                "batch webhook cleanup reached per-run budget",
                extra={
                    "max_rows_per_run": int(
                        self.config.webhook_cleanup_max_rows_per_run
                    ),
                },
            )
        return deleted

    async def run(self) -> None:
        self._running = True
        while self._running and not self._stop_event.is_set():
            iteration_failed = False
            try:
                await self.process_once()
            except Exception:
                iteration_failed = True
                logger.exception("batch cleanup iteration failed")
                if not self._running:
                    break
            if not self._running or self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(
                        0.0,
                        float(
                            self.config.failure_interval_seconds
                            if iteration_failed
                            else self.config.interval_seconds
                        ),
                    ),
                )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()

    async def process_once(self) -> tuple[int, int]:
        now = datetime.now(tz=UTC)
        deleted_jobs = 0
        deleted_files = 0
        deleted_webhooks = {"delivered": 0, "failed": 0}
        deleted_webhooks = await self._cleanup_webhook_deliveries(now=now)

        for _ in range(max(1, min(int(self.config.scan_limit), 1_000))):
            try:
                deleted = await self.repository.cleanup_next_expired_terminal_job(
                    now=now,
                )
            except BatchWebhookOwnershipConflictError as exc:
                logger.warning(
                    "batch metadata cleanup failed",
                    extra={
                        "reason": "webhook_ownership_conflict",
                        "conflict_count": exc.conflict_count,
                        "committed_jobs": deleted_jobs,
                    },
                )
                break
            except Exception:
                # Ownership repair and job deletion share one transaction. Stop
                # this pass, while retaining the count from earlier transactions.
                logger.warning(
                    "batch metadata cleanup failed",
                    extra={
                        "reason": "repository_error",
                        "committed_jobs": deleted_jobs,
                    },
                )
                break
            if not deleted:
                break
            deleted_jobs += 1

        try:
            ownership_conflicts = (
                await self.repository.count_expired_terminal_job_ownership_conflicts(
                    now=now,
                    limit=self.config.scan_limit,
                )
            )
        except Exception:
            ownership_conflicts = 0
        if ownership_conflicts:
            logger.warning(
                "batch metadata cleanup skipped ownership conflicts",
                extra={
                    "reason": "webhook_ownership_conflict",
                    "conflict_count": ownership_conflicts,
                },
            )

        expired_files = await self.repository.list_expired_unreferenced_files(
            now=now,
            limit=self.config.scan_limit,
        )
        for file_record in expired_files:
            try:
                await self._storage_for_backend(file_record.storage_backend).delete(file_record.storage_key)
            except Exception as exc:
                increment_batch_artifact_failure(
                    operation="delete",
                    backend=str(file_record.storage_backend or getattr(self.storage, "backend_name", "unknown")),
                )
                logger.warning("batch artifact delete failed file_id=%s error=%s", file_record.file_id, exc)
                continue
            await self.repository.delete_file(file_record.file_id)
            deleted_files += 1

        deleted_webhook_count = sum(deleted_webhooks.values())
        if deleted_jobs or deleted_files or deleted_webhook_count:
            logger.info(
                "batch GC deleted records",
                extra={
                    "jobs": deleted_jobs,
                    "files": deleted_files,
                    "webhook_delivered": deleted_webhooks.get("delivered", 0),
                    "webhook_failed": deleted_webhooks.get("failed", 0),
                },
            )
        await self._refresh_batch_runtime_metrics(now=now)
        return deleted_jobs, deleted_files
