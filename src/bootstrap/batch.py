from __future__ import annotations

import asyncio
import contextlib
from asyncio import Task, create_task
from dataclasses import dataclass
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.batch import BatchCleanupConfig, BatchRetentionCleanupWorker, BatchRepository
from src.batch.service import BatchService
from src.batch.storage import LocalBatchArtifactStorage, S3BatchArtifactStorage
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.services.model_visibility import normalize_callable_target_policy_mode


@dataclass
class BatchRuntime:
    worker: BatchExecutorWorker | None = None
    worker_task: Task[None] | None = None
    gc_worker: BatchRetentionCleanupWorker | None = None
    gc_task: Task[None] | None = None
    statuses: tuple[BootstrapStatus, ...] = ()


def _build_s3_batch_storage(cfg: Any) -> S3BatchArtifactStorage:
    general = cfg.general_settings
    bucket = str(getattr(general, "embeddings_batch_s3_bucket", "") or "").strip()
    if not bucket:
        raise RuntimeError("embeddings_batch_s3_bucket must be configured when embeddings_batch_storage_backend='s3'")
    try:
        return S3BatchArtifactStorage(
            bucket=bucket,
            region=str(getattr(general, "embeddings_batch_s3_region", "us-east-1") or "us-east-1"),
            prefix=str(getattr(general, "embeddings_batch_s3_prefix", "deltallm/batch-artifacts") or ""),
            endpoint_url=getattr(general, "embeddings_batch_s3_endpoint_url", None),
            access_key_id=getattr(general, "embeddings_batch_s3_access_key_id", None),
            secret_access_key=getattr(general, "embeddings_batch_s3_secret_access_key", None),
            spool_max_bytes=int(getattr(general, "embeddings_batch_s3_spool_max_bytes", 8_388_608) or 8_388_608),
        )
    except ImportError as exc:
        raise RuntimeError(
            "S3 batch storage requires the 'batch-s3' optional dependency; install with `pip install .[batch-s3]`"
        ) from exc


def _build_batch_storage_registry(cfg: Any) -> dict[str, Any]:
    general = cfg.general_settings
    registry: dict[str, Any] = {
        "local": LocalBatchArtifactStorage(general.embeddings_batch_storage_dir),
    }
    bucket = str(getattr(general, "embeddings_batch_s3_bucket", "") or "").strip()
    if bucket:
        try:
            registry["s3"] = _build_s3_batch_storage(cfg)
        except RuntimeError:
            backend = str(getattr(general, "embeddings_batch_storage_backend", "local") or "local").strip().lower()
            if backend == "s3":
                raise
    return registry


def _build_batch_storage(cfg: Any, storage_registry: dict[str, Any]):
    general = cfg.general_settings
    backend = str(getattr(general, "embeddings_batch_storage_backend", "local") or "local").strip().lower()
    storage = storage_registry.get(backend)
    if storage is None:
        if backend == "s3":
            return _build_s3_batch_storage(cfg)
        raise RuntimeError(f"Unsupported embeddings batch storage backend: {backend}")
    return storage


async def init_batch_runtime(app: Any, cfg: Any, repository: BatchRepository) -> BatchRuntime:
    runtime = BatchRuntime()

    if not cfg.general_settings.embeddings_batch_enabled:
        app.state.batch_storage = None
        app.state.batch_storage_registry = None
        app.state.batch_service = None
        runtime.statuses = (BootstrapStatus("embeddings_batch", "disabled"),)
        return runtime

    batch_storage_registry = _build_batch_storage_registry(cfg)
    batch_storage = _build_batch_storage(cfg, batch_storage_registry)
    app.state.batch_storage = batch_storage
    app.state.batch_storage_registry = batch_storage_registry
    app.state.batch_service = BatchService(
        repository=repository,
        storage=batch_storage,
        storage_registry=batch_storage_registry,
        metadata_retention_days=cfg.general_settings.batch_metadata_retention_days,
        storage_chunk_size=cfg.general_settings.embeddings_batch_storage_chunk_size,
        create_buffer_size=cfg.general_settings.embeddings_batch_create_buffer_size,
        max_file_bytes=cfg.general_settings.embeddings_batch_max_file_bytes,
        max_items_per_batch=cfg.general_settings.embeddings_batch_max_items_per_batch,
        max_line_bytes=cfg.general_settings.embeddings_batch_max_line_bytes,
        max_pending_batches_per_scope=cfg.general_settings.embeddings_batch_max_pending_batches_per_scope,
        callable_target_grant_service=getattr(app.state, "callable_target_grant_service", None),
        callable_target_scope_policy_mode=normalize_callable_target_policy_mode(
            getattr(cfg.general_settings, "callable_target_scope_policy_mode", "enforce")
        ),
    )

    if cfg.general_settings.embeddings_batch_worker_enabled:
        runtime.worker = BatchExecutorWorker(
            app=app,
            repository=repository,
            storage=batch_storage,
            config=BatchWorkerConfig(
                worker_id=f"worker-{id(app)}",
                poll_interval_seconds=cfg.general_settings.embeddings_batch_poll_interval_seconds,
                heartbeat_interval_seconds=cfg.general_settings.embeddings_batch_heartbeat_interval_seconds,
                job_lease_seconds=cfg.general_settings.embeddings_batch_job_lease_seconds,
                item_lease_seconds=cfg.general_settings.embeddings_batch_item_lease_seconds,
                finalization_retry_delay_seconds=cfg.general_settings.embeddings_batch_finalization_retry_delay_seconds,
                worker_concurrency=cfg.general_settings.embeddings_batch_worker_concurrency,
                item_buffer_multiplier=cfg.general_settings.embeddings_batch_item_buffer_multiplier,
                finalization_page_size=cfg.general_settings.embeddings_batch_finalization_page_size,
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
            storage_registry=batch_storage_registry,
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
