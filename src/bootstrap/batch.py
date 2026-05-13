from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from asyncio import Task, create_task
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from src.batch import (
    BatchCleanupConfig,
    BatchRetentionCleanupWorker,
    BatchRepository,
    BatchSchedulerBackfillConfig,
    BatchSchedulerBackfillWorker,
)
from src.batch.backpressure import BatchBackpressureCoordinator
from src.batch.create.admin_service import BatchCreateSessionAdminService
from src.batch.create.cleanup import BatchCreateSessionCleanupConfig, BatchCreateSessionCleanupWorker
from src.batch.create.promoter import BatchCreateSessionPromoter
from src.batch.create.service import BatchCreateSessionService
from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.create.session_stager import BatchCreateSessionStager
from src.batch.create.staging import BatchCreateArtifactStorageBackend
from src.batch.completion_outbox import BatchCompletionOutboxWorker, BatchCompletionOutboxWorkerConfig
from src.batch.service import BatchService
from src.batch.scheduling import BatchModelCapacityConfig, BatchModelCapacityResolver, BatchTenantFairShareConfig
from src.batch.storage import LocalBatchArtifactStorage, S3BatchArtifactStorage
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.bootstrap.status import BootstrapStatus
from src.services.model_visibility import normalize_callable_target_policy_mode

logger = logging.getLogger(__name__)

# Upper bound on how long shutdown_batch_runtime waits for an in-flight
# process_once iteration to drain before falling back to hard-cancelling the
# worker task. Kept conservative so k8s pod termination grace periods are
# respected; the worker loop's own idle poll is sub-second.
_WORKER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 30.0
_BATCH_WORKER_BOOT_ID = uuid4().hex[:12]


def _safe_worker_id_part(value: object, *, fallback: str) -> str:
    safe = "".join(
        char if char.isascii() and (char.isalnum() or char in {"-", "_", "."}) else "-"
        for char in str(value or "").strip()
    ).strip("-._")
    return safe or fallback


def _batch_worker_id(role: str) -> str:
    return "-".join(
        (
            _safe_worker_id_part(role, fallback="batch-worker"),
            _safe_worker_id_part(socket.gethostname(), fallback="unknown-host"),
            str(os.getpid()),
            _BATCH_WORKER_BOOT_ID,
        )
    )


def _batch_scheduler_active_enabled_for_creation(general: Any) -> bool:
    return bool(
        getattr(general, "embeddings_batch_scheduler_enabled", False)
        or getattr(general, "embeddings_batch_tenant_fair_share_enabled", False)
    )


@dataclass
class BatchRuntime:
    backpressure: BatchBackpressureCoordinator | None = None
    model_capacity_resolver: BatchModelCapacityResolver | None = None
    tenant_fair_share_config: BatchTenantFairShareConfig | None = None
    worker: BatchExecutorWorker | None = None
    worker_task: Task[None] | None = None
    completion_outbox_worker: BatchCompletionOutboxWorker | None = None
    completion_outbox_task: Task[None] | None = None
    gc_worker: BatchRetentionCleanupWorker | None = None
    gc_task: Task[None] | None = None
    create_session_staging_backend: BatchCreateArtifactStorageBackend | None = None
    create_session_promoter: BatchCreateSessionPromoter | None = None
    create_session_admin_service: BatchCreateSessionAdminService | None = None
    create_session_cleanup_worker: BatchCreateSessionCleanupWorker | None = None
    create_session_cleanup_task: Task[None] | None = None
    scheduler_backfill_worker: BatchSchedulerBackfillWorker | None = None
    scheduler_backfill_task: Task[None] | None = None
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
    backend = str(getattr(general, "embeddings_batch_storage_backend", "local") or "local").strip().lower()
    registry: dict[str, Any] = {}
    try:
        registry["local"] = LocalBatchArtifactStorage(general.embeddings_batch_storage_dir)
    except Exception:
        if backend == "local":
            raise
        logger.warning(
            "batch local storage backend unavailable for legacy artifact routing path=%s",
            general.embeddings_batch_storage_dir,
            exc_info=True,
        )
    bucket = str(getattr(general, "embeddings_batch_s3_bucket", "") or "").strip()
    if bucket:
        try:
            registry["s3"] = _build_s3_batch_storage(cfg)
        except RuntimeError:
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

def _build_create_session_staging_backend(
    cfg: Any,
    *,
    storage: Any,
    storage_registry: dict[str, Any],
) -> BatchCreateArtifactStorageBackend:
    general = cfg.general_settings
    return BatchCreateArtifactStorageBackend(
        storage=storage,
        storage_registry=storage_registry,
        chunk_size=general.embeddings_batch_storage_chunk_size,
        max_line_bytes=general.embeddings_batch_max_line_bytes,
    )


def _build_create_session_cleanup_worker(
    cfg: Any,
    *,
    session_repository: BatchCreateSessionRepository,
    staging_backend: BatchCreateArtifactStorageBackend,
) -> BatchCreateSessionCleanupWorker:
    general = cfg.general_settings
    cleanup_worker = BatchCreateSessionCleanupWorker(
        repository=session_repository,
        staging=staging_backend,
        config=BatchCreateSessionCleanupConfig(
            interval_seconds=general.embeddings_batch_create_session_cleanup_interval_seconds,
            scan_limit=general.embeddings_batch_create_session_cleanup_scan_limit,
            orphan_grace_seconds=general.embeddings_batch_create_stage_orphan_grace_seconds,
            completed_retention_seconds=general.embeddings_batch_create_session_completed_retention_seconds,
            retryable_retention_seconds=general.embeddings_batch_create_session_retryable_retention_seconds,
            failed_retention_seconds=general.embeddings_batch_create_session_failed_retention_seconds,
        )
    )
    return cleanup_worker


def _build_create_session_promoter(
    cfg: Any,
    *,
    repository: BatchRepository,
    staging_backend: BatchCreateArtifactStorageBackend,
    model_group_resolver: Any | None = None,
) -> BatchCreateSessionPromoter:
    general = cfg.general_settings
    return BatchCreateSessionPromoter(
        repository=repository,
        staging=staging_backend,
        metadata_retention_days=general.batch_metadata_retention_days,
        max_pending_batches_per_scope=general.embeddings_batch_max_pending_batches_per_scope,
        insert_chunk_size=general.embeddings_batch_create_promotion_insert_chunk_size,
        soft_precheck_enabled=general.embeddings_batch_create_soft_precheck_enabled,
        tx_max_wait_seconds=general.embeddings_batch_create_promotion_tx_max_wait_seconds,
        tx_timeout_seconds=general.embeddings_batch_create_promotion_tx_timeout_seconds,
        model_group_resolver=model_group_resolver,
        scheduler_enabled=_batch_scheduler_active_enabled_for_creation(general),
        scheduler_shadow_enabled=getattr(general, "embeddings_batch_scheduler_shadow_enabled", False),
        strict_model_homogeneity_enabled=getattr(
            general,
            "embeddings_batch_scheduler_strict_model_homogeneity_enabled",
            False,
        ),
        default_service_tier=getattr(general, "embeddings_batch_scheduler_default_service_tier", "standard"),
        tenant_scope_preference=getattr(
            general,
            "embeddings_batch_tenant_scope_preference",
            "organization,team,api_key,user",
        ),
        tenant_max_queued_work_units=getattr(general, "embeddings_batch_tenant_max_queued_work_units", 0),
    )


async def init_batch_runtime(app: Any, cfg: Any, repository: BatchRepository) -> BatchRuntime:
    runtime = BatchRuntime()

    if not cfg.general_settings.embeddings_batch_enabled:
        app.state.batch_storage = None
        app.state.batch_storage_registry = None
        app.state.batch_service = None
        app.state.batch_create_session_repository = None
        app.state.batch_create_staging_backend = None
        app.state.batch_create_promoter = None
        app.state.batch_create_session_service = None
        app.state.batch_create_session_admin_service = None
        app.state.batch_create_session_cleanup_worker = None
        app.state.batch_scheduler_backfill_worker = None
        app.state.batch_backpressure = None
        app.state.batch_model_capacity_resolver = None
        app.state.batch_tenant_fair_share_config = None
        runtime.statuses = (BootstrapStatus("embeddings_batch", "disabled"),)
        return runtime

    batch_storage_registry = _build_batch_storage_registry(cfg)
    batch_storage = _build_batch_storage(cfg, batch_storage_registry)
    app.state.batch_storage = batch_storage
    app.state.batch_storage_registry = batch_storage_registry
    app.state.batch_create_session_repository = getattr(repository, "create_sessions", None)
    app.state.batch_create_staging_backend = None
    app.state.batch_create_promoter = None
    app.state.batch_create_session_service = None
    app.state.batch_create_session_admin_service = None
    app.state.batch_create_session_cleanup_worker = None
    app.state.batch_scheduler_backfill_worker = None
    model_group_resolver = getattr(app.state, "router", None)
    set_repository_resolver = getattr(repository, "set_model_group_resolver", None)
    if callable(set_repository_resolver):
        set_repository_resolver(model_group_resolver)
    runtime.backpressure = BatchBackpressureCoordinator(
        redis_client=getattr(app.state, "redis", None),
        enabled=cfg.general_settings.embeddings_batch_model_group_backpressure_enabled,
        min_delay_seconds=cfg.general_settings.embeddings_batch_model_group_backpressure_min_seconds,
        max_delay_seconds=cfg.general_settings.embeddings_batch_model_group_backpressure_max_seconds,
    )
    app.state.batch_backpressure = runtime.backpressure
    model_capacity_config = BatchModelCapacityConfig.from_settings(cfg.general_settings)
    tenant_fair_share_config = BatchTenantFairShareConfig.from_settings(cfg.general_settings)
    set_repository_tenant_scope_preference = getattr(repository, "set_tenant_scope_preference", None)
    if callable(set_repository_tenant_scope_preference):
        set_repository_tenant_scope_preference(tenant_fair_share_config.tenant_scope_preference)
    runtime.tenant_fair_share_config = tenant_fair_share_config
    if model_capacity_config.enabled:
        runtime.model_capacity_resolver = BatchModelCapacityResolver(
            repository=repository,
            config=model_capacity_config,
            router=getattr(app.state, "router", None),
            router_state_backend=getattr(app.state, "router_state_backend", None),
            backpressure=runtime.backpressure,
        )
    app.state.batch_model_capacity_resolver = runtime.model_capacity_resolver
    app.state.batch_tenant_fair_share_config = tenant_fair_share_config
    app.state.batch_service = BatchService(
        repository=repository,
        storage=batch_storage,
        storage_registry=batch_storage_registry,
        metadata_retention_days=cfg.general_settings.batch_metadata_retention_days,
        storage_chunk_size=cfg.general_settings.embeddings_batch_storage_chunk_size,
        max_file_bytes=cfg.general_settings.embeddings_batch_max_file_bytes,
        max_items_per_batch=cfg.general_settings.embeddings_batch_max_items_per_batch,
        max_line_bytes=cfg.general_settings.embeddings_batch_max_line_bytes,
        callable_target_grant_service=getattr(app.state, "callable_target_grant_service", None),
        callable_target_scope_policy_mode=normalize_callable_target_policy_mode(
            getattr(cfg.general_settings, "callable_target_scope_policy_mode", "enforce")
        ),
        model_group_resolver=model_group_resolver,
    )

    session_repository = app.state.batch_create_session_repository
    if session_repository is None:
        raise RuntimeError("Batch create-session repository is unavailable while embeddings batching is enabled")
    try:
        await session_repository.ensure_schema_ready()
    except Exception as exc:
        raise RuntimeError("Batch create-session schema is unavailable while embeddings batching is enabled") from exc

    if cfg.general_settings.embeddings_batch_worker_enabled:
        worker_kwargs = {
            "app": app,
            "repository": repository,
            "storage": batch_storage,
            "config": BatchWorkerConfig(
                worker_id=_batch_worker_id("batch-executor"),
                poll_interval_seconds=cfg.general_settings.embeddings_batch_poll_interval_seconds,
                heartbeat_interval_seconds=cfg.general_settings.embeddings_batch_heartbeat_interval_seconds,
                job_lease_seconds=cfg.general_settings.embeddings_batch_job_lease_seconds,
                item_lease_seconds=cfg.general_settings.embeddings_batch_item_lease_seconds,
                finalization_retry_delay_seconds=cfg.general_settings.embeddings_batch_finalization_retry_delay_seconds,
                worker_concurrency=cfg.general_settings.embeddings_batch_worker_concurrency,
                item_buffer_multiplier=cfg.general_settings.embeddings_batch_item_buffer_multiplier,
                finalization_page_size=cfg.general_settings.embeddings_batch_finalization_page_size,
                item_claim_limit=cfg.general_settings.embeddings_batch_item_claim_limit,
                scheduler_claim_mode=cfg.general_settings.embeddings_batch_scheduler_claim_mode,
                work_claim_max_items=cfg.general_settings.embeddings_batch_work_claim_max_items,
                work_claim_max_work_units=cfg.general_settings.embeddings_batch_work_claim_max_work_units,
                work_claim_min_items_for_microbatch=(
                    cfg.general_settings.embeddings_batch_work_claim_min_items_for_microbatch
                ),
                model_capacity_enabled=model_capacity_config.enabled,
                scheduler_shadow_enabled=getattr(
                    cfg.general_settings,
                    "embeddings_batch_scheduler_shadow_enabled",
                    False,
                ),
                tenant_fair_share_enabled=tenant_fair_share_config.enabled,
                tenant_fair_share_base_quantum_work_units=tenant_fair_share_config.base_quantum_work_units,
                tenant_fair_share_max_deficit_multiplier=tenant_fair_share_config.max_deficit_multiplier,
                tenant_max_in_flight_work_units=tenant_fair_share_config.tenant_max_in_flight_work_units,
                tenant_fair_share_disabled_model_groups=tenant_fair_share_config.disabled_model_groups,
                finalization_first=cfg.general_settings.embeddings_batch_finalization_first,
                max_attempts=cfg.general_settings.embeddings_batch_max_attempts,
                retry_initial_seconds=cfg.general_settings.embeddings_batch_retry_initial_seconds,
                retry_max_seconds=cfg.general_settings.embeddings_batch_retry_max_seconds,
                retry_multiplier=cfg.general_settings.embeddings_batch_retry_multiplier,
                retry_jitter=cfg.general_settings.embeddings_batch_retry_jitter,
                microbatch_retry_enabled=cfg.general_settings.embeddings_batch_microbatch_retry_enabled,
                microbatch_max_group_retries=cfg.general_settings.embeddings_batch_microbatch_max_group_retries,
                microbatch_min_reduced_size=cfg.general_settings.embeddings_batch_microbatch_min_reduced_size,
                microbatch_reduce_factor=cfg.general_settings.embeddings_batch_microbatch_reduce_factor,
                completed_artifact_retention_days=cfg.general_settings.batch_completed_artifact_retention_days,
                failed_artifact_retention_days=cfg.general_settings.batch_failed_artifact_retention_days,
            ),
        }
        if runtime.model_capacity_resolver is not None:
            worker_kwargs["model_capacity_resolver"] = runtime.model_capacity_resolver
        runtime.worker = BatchExecutorWorker(**worker_kwargs)
        runtime.worker_task = create_task(runtime.worker.run())

    if cfg.general_settings.embeddings_batch_completion_outbox_worker_enabled:
        runtime.completion_outbox_worker = BatchCompletionOutboxWorker(
            app=app,
            repository=repository,
            config=BatchCompletionOutboxWorkerConfig(
                worker_id=_batch_worker_id("batch-completion-outbox"),
                poll_interval_seconds=cfg.general_settings.embeddings_batch_poll_interval_seconds,
                max_batch_size=max(10, int(cfg.general_settings.embeddings_batch_item_claim_limit or 20)),
                max_concurrency=min(8, max(1, int(cfg.general_settings.embeddings_batch_worker_concurrency or 4))),
                lease_seconds=max(15, int(cfg.general_settings.embeddings_batch_item_lease_seconds or 60)),
                heartbeat_interval_seconds=max(
                    1.0,
                    float(cfg.general_settings.embeddings_batch_heartbeat_interval_seconds or 10.0),
                ),
            ),
        )
        runtime.completion_outbox_task = create_task(runtime.completion_outbox_worker.run())

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

    if getattr(cfg.general_settings, "embeddings_batch_scheduler_backfill_enabled", False):
        runtime.scheduler_backfill_worker = BatchSchedulerBackfillWorker(
            repository=repository,
            config=BatchSchedulerBackfillConfig(
                interval_seconds=getattr(
                    cfg.general_settings,
                    "embeddings_batch_scheduler_backfill_interval_seconds",
                    60.0,
                ),
                scan_limit=getattr(
                    cfg.general_settings,
                    "embeddings_batch_scheduler_backfill_scan_limit",
                    500,
                ),
            ),
        )
        app.state.batch_scheduler_backfill_worker = runtime.scheduler_backfill_worker
        runtime.scheduler_backfill_task = create_task(runtime.scheduler_backfill_worker.run())

    runtime.create_session_staging_backend = _build_create_session_staging_backend(
        cfg,
        storage=batch_storage,
        storage_registry=batch_storage_registry,
    )
    app.state.batch_create_staging_backend = runtime.create_session_staging_backend
    runtime.create_session_promoter = _build_create_session_promoter(
        cfg,
        repository=repository,
        staging_backend=runtime.create_session_staging_backend,
        model_group_resolver=model_group_resolver,
    )
    app.state.batch_create_promoter = runtime.create_session_promoter
    runtime.create_session_admin_service = BatchCreateSessionAdminService(
        repository=session_repository,
        promoter=runtime.create_session_promoter,
        staging=runtime.create_session_staging_backend,
    )
    app.state.batch_create_session_admin_service = runtime.create_session_admin_service

    app.state.batch_create_session_service = BatchCreateSessionService(
        repository=repository,
        create_session_repository=session_repository,
        stager=BatchCreateSessionStager(
            repository=session_repository,
            staging=runtime.create_session_staging_backend,
        ),
        promoter=runtime.create_session_promoter,
        storage_registry=batch_storage_registry,
        max_file_bytes=cfg.general_settings.embeddings_batch_max_file_bytes,
        max_items_per_batch=cfg.general_settings.embeddings_batch_max_items_per_batch,
        max_line_bytes=cfg.general_settings.embeddings_batch_max_line_bytes,
        storage_chunk_size=cfg.general_settings.embeddings_batch_storage_chunk_size,
        max_pending_batches_per_scope=cfg.general_settings.embeddings_batch_max_pending_batches_per_scope,
        callable_target_grant_service=getattr(app.state, "callable_target_grant_service", None),
        callable_target_scope_policy_mode=normalize_callable_target_policy_mode(
            getattr(cfg.general_settings, "callable_target_scope_policy_mode", "enforce")
        ),
        idempotency_enabled=cfg.general_settings.embeddings_batch_create_idempotency_enabled,
        model_group_resolver=model_group_resolver,
        scheduler_enabled=_batch_scheduler_active_enabled_for_creation(cfg.general_settings),
        scheduler_shadow_enabled=getattr(cfg.general_settings, "embeddings_batch_scheduler_shadow_enabled", False),
        strict_model_homogeneity_enabled=getattr(
            cfg.general_settings,
            "embeddings_batch_scheduler_strict_model_homogeneity_enabled",
            False,
        ),
        default_service_tier=getattr(cfg.general_settings, "embeddings_batch_scheduler_default_service_tier", "standard"),
    )
    app.state.batch_service.bind_create_session_service(app.state.batch_create_session_service)

    if cfg.general_settings.embeddings_batch_create_session_cleanup_enabled:
        assert runtime.create_session_staging_backend is not None
        runtime.create_session_cleanup_worker = _build_create_session_cleanup_worker(
            cfg,
            session_repository=session_repository,
            staging_backend=runtime.create_session_staging_backend,
        )
        app.state.batch_create_session_cleanup_worker = runtime.create_session_cleanup_worker
        runtime.create_session_cleanup_task = create_task(runtime.create_session_cleanup_worker.run())

    runtime.statuses = (
        BootstrapStatus("embeddings_batch", "ready"),
        BootstrapStatus("embeddings_batch_worker", "ready" if runtime.worker is not None else "disabled"),
        BootstrapStatus("embeddings_batch_completion_outbox", "ready" if runtime.completion_outbox_worker is not None else "disabled"),
        BootstrapStatus("embeddings_batch_gc", "ready" if runtime.gc_worker is not None else "disabled"),
        BootstrapStatus(
            "embeddings_batch_scheduler_backfill",
            "ready" if runtime.scheduler_backfill_worker is not None else "disabled",
        ),
        BootstrapStatus(
            "embeddings_batch_create_session_admin",
            "ready" if runtime.create_session_admin_service is not None else "disabled",
        ),
        BootstrapStatus(
            "embeddings_batch_create_session_cleanup",
            "ready" if runtime.create_session_cleanup_worker is not None else "disabled",
        ),
    )
    return runtime


async def _drain_worker_task(
    task: Task[None],
    *,
    label: str,
    timeout: float,
) -> None:
    """Wait for a worker's run() loop to exit naturally after stop() flipped
    its _running flag. Falls back to cancellation if draining exceeds the
    timeout so pod termination stays bounded.
    """
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return
    except asyncio.TimeoutError:
        logger.warning(
            "%s drain timed out after %.1fs; cancelling in-flight iteration",
            label,
            timeout,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("%s drain raised", label)
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def shutdown_batch_runtime(runtime: BatchRuntime) -> None:
    if runtime.worker is not None:
        runtime.worker.stop()
    if runtime.worker_task is not None:
        await _drain_worker_task(
            runtime.worker_task,
            label="batch worker",
            timeout=_WORKER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
        )

    if runtime.completion_outbox_worker is not None:
        runtime.completion_outbox_worker.stop()
    if runtime.completion_outbox_task is not None:
        await _drain_worker_task(
            runtime.completion_outbox_task,
            label="batch completion outbox worker",
            timeout=5.0,
        )

    if runtime.gc_worker is not None:
        runtime.gc_worker.stop()
    if runtime.gc_task is not None:
        # GC has no mid-iteration lease risk; short drain then cancel.
        await _drain_worker_task(
            runtime.gc_task,
            label="batch gc worker",
            timeout=5.0,
        )

    if runtime.scheduler_backfill_worker is not None:
        runtime.scheduler_backfill_worker.stop()
    if runtime.scheduler_backfill_task is not None:
        await _drain_worker_task(
            runtime.scheduler_backfill_task,
            label="batch scheduler backfill worker",
            timeout=5.0,
        )

    if runtime.create_session_cleanup_worker is not None:
        runtime.create_session_cleanup_worker.stop()
    if runtime.create_session_cleanup_task is not None:
        await _drain_worker_task(
            runtime.create_session_cleanup_task,
            label="batch create-session cleanup worker",
            timeout=5.0,
        )
