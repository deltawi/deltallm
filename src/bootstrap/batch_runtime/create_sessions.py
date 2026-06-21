from __future__ import annotations

from asyncio import create_task
from typing import Any

from src.batch import BatchRepository
from src.batch.create.admin_service import BatchCreateSessionAdminService
from src.batch.create.cleanup import BatchCreateSessionCleanupConfig, BatchCreateSessionCleanupWorker
from src.batch.create.promoter import BatchCreateSessionPromoter
from src.batch.create.service import BatchCreateSessionService
from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.create.session_stager import BatchCreateSessionStager
from src.batch.create.staging import BatchCreateArtifactStorageBackend
from src.batch.scheduling import resolve_scheduler_modes_from_settings
from src.batch.webhooks import BatchWebhookCipher
from src.bootstrap.batch_runtime.core import BatchCoreComponents
from src.bootstrap.batch_runtime.runtime import BatchRuntime
from src.bootstrap.batch_runtime.scheduler import batch_scheduler_active_enabled_for_creation
from src.services.model_visibility import normalize_callable_target_policy_mode


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
    return BatchCreateSessionCleanupWorker(
        repository=session_repository,
        staging=staging_backend,
        config=BatchCreateSessionCleanupConfig(
            interval_seconds=general.embeddings_batch_create_session_cleanup_interval_seconds,
            scan_limit=general.embeddings_batch_create_session_cleanup_scan_limit,
            orphan_grace_seconds=general.embeddings_batch_create_stage_orphan_grace_seconds,
            completed_retention_seconds=(
                general.embeddings_batch_create_session_completed_retention_seconds
            ),
            retryable_retention_seconds=(
                general.embeddings_batch_create_session_retryable_retention_seconds
            ),
            failed_retention_seconds=(
                general.embeddings_batch_create_session_failed_retention_seconds
            ),
        ),
    )


def _build_create_session_promoter(
    cfg: Any,
    *,
    repository: BatchRepository,
    staging_backend: BatchCreateArtifactStorageBackend,
    model_group_resolver: Any | None = None,
) -> BatchCreateSessionPromoter:
    general = cfg.general_settings
    scheduler_modes = resolve_scheduler_modes_from_settings(general)
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
        scheduler_enabled=batch_scheduler_active_enabled_for_creation(general),
        scheduler_shadow_enabled=scheduler_modes.shadow_mode != "none",
        scheduler_mode=scheduler_modes.active_mode,
        scheduler_shadow_mode=scheduler_modes.shadow_mode,
        strict_model_homogeneity_enabled=getattr(
            general,
            "embeddings_batch_scheduler_strict_model_homogeneity_enabled",
            False,
        ),
        default_service_tier=getattr(
            general,
            "embeddings_batch_scheduler_default_service_tier",
            "standard",
        ),
        tenant_scope_preference=getattr(
            general,
            "embeddings_batch_tenant_scope_preference",
            "organization,team,api_key,user",
        ),
        tenant_max_queued_work_units=getattr(
            general,
            "embeddings_batch_tenant_max_queued_work_units",
            0,
        ),
    )


def initialize_create_session_services(
    app: Any,
    cfg: Any,
    repository: BatchRepository,
    runtime: BatchRuntime,
    core: BatchCoreComponents,
    webhook_cipher: BatchWebhookCipher | None,
) -> None:
    general = cfg.general_settings
    runtime.create_session_staging_backend = _build_create_session_staging_backend(
        cfg,
        storage=core.storage,
        storage_registry=core.storage_registry,
    )
    app.state.batch_create_staging_backend = runtime.create_session_staging_backend
    runtime.create_session_promoter = _build_create_session_promoter(
        cfg,
        repository=repository,
        staging_backend=runtime.create_session_staging_backend,
        model_group_resolver=core.model_group_resolver,
    )
    app.state.batch_create_promoter = runtime.create_session_promoter
    runtime.create_session_admin_service = BatchCreateSessionAdminService(
        repository=core.session_repository,
        promoter=runtime.create_session_promoter,
        staging=runtime.create_session_staging_backend,
    )
    app.state.batch_create_session_admin_service = runtime.create_session_admin_service

    app.state.batch_create_session_service = BatchCreateSessionService(
        repository=repository,
        create_session_repository=core.session_repository,
        stager=BatchCreateSessionStager(
            repository=core.session_repository,
            staging=runtime.create_session_staging_backend,
        ),
        promoter=runtime.create_session_promoter,
        storage_registry=core.storage_registry,
        max_file_bytes=general.embeddings_batch_max_file_bytes,
        max_items_per_batch=general.embeddings_batch_max_items_per_batch,
        max_line_bytes=general.embeddings_batch_max_line_bytes,
        storage_chunk_size=general.embeddings_batch_storage_chunk_size,
        max_pending_batches_per_scope=general.embeddings_batch_max_pending_batches_per_scope,
        callable_target_grant_service=getattr(app.state, "callable_target_grant_service", None),
        tier_policy_service=getattr(app.state, "tier_policy_service", None),
        callable_target_scope_policy_mode=normalize_callable_target_policy_mode(
            getattr(general, "callable_target_scope_policy_mode", "enforce")
        ),
        tier_policy_mode=getattr(general, "tier_policy_mode", "disabled"),
        tier_policy_missing_service_mode=getattr(
            general,
            "tier_policy_missing_service_mode",
            "fail_open",
        ),
        idempotency_enabled=general.embeddings_batch_create_idempotency_enabled,
        model_group_resolver=core.model_group_resolver,
        scheduler_enabled=batch_scheduler_active_enabled_for_creation(general),
        scheduler_shadow_enabled=core.scheduler_modes.shadow_mode != "none",
        strict_model_homogeneity_enabled=getattr(
            general,
            "embeddings_batch_scheduler_strict_model_homogeneity_enabled",
            False,
        ),
        default_service_tier=getattr(
            general,
            "embeddings_batch_scheduler_default_service_tier",
            "standard",
        ),
        webhook_enabled=getattr(general, "batch_webhook_enabled", False),
        webhook_cipher=webhook_cipher,
        webhook_allow_http=getattr(general, "batch_webhook_allow_http", False),
        webhook_allowed_ports=getattr(general, "batch_webhook_allowed_ports", [443]),
    )
    app.state.batch_service.bind_create_session_service(app.state.batch_create_session_service)


def start_create_session_cleanup(
    app: Any,
    cfg: Any,
    runtime: BatchRuntime,
    session_repository: BatchCreateSessionRepository,
) -> None:
    if not cfg.general_settings.embeddings_batch_create_session_cleanup_enabled:
        return
    assert runtime.create_session_staging_backend is not None
    runtime.create_session_cleanup_worker = _build_create_session_cleanup_worker(
        cfg,
        session_repository=session_repository,
        staging_backend=runtime.create_session_staging_backend,
    )
    app.state.batch_create_session_cleanup_worker = runtime.create_session_cleanup_worker
    runtime.create_session_cleanup_task = create_task(runtime.create_session_cleanup_worker.run())
