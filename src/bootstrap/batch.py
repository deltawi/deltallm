from __future__ import annotations

import asyncio
import contextlib
from asyncio import Task, create_task
from dataclasses import dataclass
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.batch import BatchCleanupConfig, BatchRetentionCleanupWorker, BatchRepository
from src.batch.service import BatchService
from src.batch.storage import LocalBatchArtifactStorage
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig


@dataclass
class BatchRuntime:
    worker: BatchExecutorWorker | None = None
    worker_task: Task[None] | None = None
    gc_worker: BatchRetentionCleanupWorker | None = None
    gc_task: Task[None] | None = None
    statuses: tuple[BootstrapStatus, ...] = ()


async def init_batch_runtime(app: Any, cfg: Any, repository: BatchRepository) -> BatchRuntime:
    runtime = BatchRuntime()

    if not cfg.general_settings.embeddings_batch_enabled:
        app.state.batch_storage = None
        app.state.batch_service = None
        runtime.statuses = (BootstrapStatus("embeddings_batch", "disabled"),)
        return runtime

    batch_storage = LocalBatchArtifactStorage(cfg.general_settings.embeddings_batch_storage_dir)
    app.state.batch_storage = batch_storage
    app.state.batch_service = BatchService(
        repository=repository,
        storage=batch_storage,
        metadata_retention_days=cfg.general_settings.batch_metadata_retention_days,
    )

    if cfg.general_settings.embeddings_batch_worker_enabled:
        runtime.worker = BatchExecutorWorker(
            app=app,
            repository=repository,
            storage=batch_storage,
            config=BatchWorkerConfig(
                worker_id=f"worker-{id(app)}",
                poll_interval_seconds=cfg.general_settings.embeddings_batch_poll_interval_seconds,
                item_claim_limit=cfg.general_settings.embeddings_batch_item_claim_limit,
                max_attempts=cfg.general_settings.embeddings_batch_max_attempts,
                completed_artifact_retention_days=cfg.general_settings.batch_completed_artifact_retention_days,
                failed_artifact_retention_days=cfg.general_settings.batch_failed_artifact_retention_days,
            ),
        )
        runtime.worker_task = create_task(runtime.worker.run())

    if cfg.general_settings.embeddings_batch_gc_enabled:
        runtime.gc_worker = BatchRetentionCleanupWorker(
            repository=repository,
            storage=batch_storage,
            config=BatchCleanupConfig(
                interval_seconds=cfg.general_settings.embeddings_batch_gc_interval_seconds,
                scan_limit=cfg.general_settings.embeddings_batch_gc_scan_limit,
            ),
        )
        runtime.gc_task = create_task(runtime.gc_worker.run())

    runtime.statuses = (
        BootstrapStatus("embeddings_batch", "ready"),
        BootstrapStatus("embeddings_batch_worker", "ready" if runtime.worker is not None else "disabled"),
        BootstrapStatus("embeddings_batch_gc", "ready" if runtime.gc_worker is not None else "disabled"),
    )
    return runtime


async def shutdown_batch_runtime(runtime: BatchRuntime) -> None:
    if runtime.worker is not None:
        runtime.worker.stop()
    if runtime.worker_task is not None:
        runtime.worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.worker_task

    if runtime.gc_worker is not None:
        runtime.gc_worker.stop()
    if runtime.gc_task is not None:
        runtime.gc_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.gc_task
