from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.batch import BatchRepository
from src.batch.backpressure import BatchBackpressureCoordinator
from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.scheduling import (
    BatchModelCapacityConfig,
    BatchModelCapacityResolver,
    BatchSchedulerModes,
    BatchSizeAgingConfig,
    BatchTenantFairShareConfig,
    resolve_scheduler_modes_from_settings,
)
from src.batch.service import BatchService
from src.bootstrap.batch_runtime.runtime import BatchRuntime
from src.bootstrap.batch_runtime.scheduler import record_startup_scheduler_rollbacks
from src.bootstrap.batch_runtime.state import configure_enabled_batch_state
from src.bootstrap.batch_runtime.storage import build_batch_storage, build_batch_storage_registry
from src.services.model_visibility import normalize_callable_target_policy_mode


@dataclass(frozen=True)
class BatchCoreComponents:
    storage: Any
    storage_registry: dict[str, Any]
    session_repository: BatchCreateSessionRepository
    model_group_resolver: Any
    scheduler_modes: BatchSchedulerModes
    model_capacity_config: BatchModelCapacityConfig
    tenant_fair_share_config: BatchTenantFairShareConfig
    size_aging_config: BatchSizeAgingConfig


async def initialize_batch_core(
    app: Any,
    cfg: Any,
    repository: BatchRepository,
    runtime: BatchRuntime,
) -> BatchCoreComponents:
    general = cfg.general_settings
    storage_registry = build_batch_storage_registry(cfg)
    storage = build_batch_storage(cfg, storage_registry)
    app.state.batch_storage = storage
    app.state.batch_storage_registry = storage_registry
    configure_enabled_batch_state(app, repository)

    model_group_resolver = getattr(app.state, "router", None)
    set_repository_resolver = getattr(repository, "set_model_group_resolver", None)
    if callable(set_repository_resolver):
        set_repository_resolver(model_group_resolver)

    runtime.backpressure = BatchBackpressureCoordinator(
        redis_client=getattr(app.state, "redis", None),
        enabled=general.embeddings_batch_model_group_backpressure_enabled,
        min_delay_seconds=general.embeddings_batch_model_group_backpressure_min_seconds,
        max_delay_seconds=general.embeddings_batch_model_group_backpressure_max_seconds,
    )
    app.state.batch_backpressure = runtime.backpressure

    scheduler_modes = resolve_scheduler_modes_from_settings(general)
    await record_startup_scheduler_rollbacks(repository, scheduler_modes)
    model_capacity_config = BatchModelCapacityConfig.from_settings(general)
    tenant_fair_share_config = BatchTenantFairShareConfig.from_settings(general)
    size_aging_config = BatchSizeAgingConfig.from_settings(general)
    set_tenant_scope_preference = getattr(repository, "set_tenant_scope_preference", None)
    if callable(set_tenant_scope_preference):
        set_tenant_scope_preference(tenant_fair_share_config.tenant_scope_preference)

    runtime.tenant_fair_share_config = tenant_fair_share_config
    runtime.size_aging_config = size_aging_config
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
    app.state.batch_size_aging_config = size_aging_config
    app.state.batch_scheduler_modes = scheduler_modes

    app.state.batch_service = BatchService(
        repository=repository,
        storage=storage,
        storage_registry=storage_registry,
        metadata_retention_days=general.batch_metadata_retention_days,
        storage_chunk_size=general.embeddings_batch_storage_chunk_size,
        max_file_bytes=general.embeddings_batch_max_file_bytes,
        max_items_per_batch=general.embeddings_batch_max_items_per_batch,
        max_line_bytes=general.embeddings_batch_max_line_bytes,
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
        model_group_resolver=model_group_resolver,
    )

    session_repository = app.state.batch_create_session_repository
    if session_repository is None:
        raise RuntimeError(
            "Batch create-session repository is unavailable while embeddings batching is enabled"
        )
    try:
        await session_repository.ensure_schema_ready()
    except Exception as exc:
        raise RuntimeError(
            "Batch create-session schema is unavailable while embeddings batching is enabled"
        ) from exc

    return BatchCoreComponents(
        storage=storage,
        storage_registry=storage_registry,
        session_repository=session_repository,
        model_group_resolver=model_group_resolver,
        scheduler_modes=scheduler_modes,
        model_capacity_config=model_capacity_config,
        tenant_fair_share_config=tenant_fair_share_config,
        size_aging_config=size_aging_config,
    )
