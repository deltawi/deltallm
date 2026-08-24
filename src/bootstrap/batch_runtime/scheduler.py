from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from src.batch import BatchRepository
from src.batch.scheduling import (
    BatchModelCapacityConfig,
    BatchModelCapacityResolver,
    BatchSchedulerModes,
    BatchSizeAgingConfig,
    BatchTenantFairShareConfig,
    normalize_scheduler_mode,
    normalize_scheduler_shadow_mode,
    resolve_scheduler_modes_from_settings,
    scheduler_rollback_events,
    set_advisory_lock_mode,
)
from src.batch.worker import BatchWorkerConfig
from src.bootstrap.batch_runtime.runtime import BatchRuntime
from src.metrics import increment_batch_scheduler_rollback

logger = logging.getLogger(__name__)

_BATCH_SCHEDULER_MODE_STATE_KEY = "batch_scheduler_modes"


def batch_scheduler_active_enabled_for_creation(general: Any) -> bool:
    modes = resolve_scheduler_modes_from_settings(general)
    return bool(modes.active_mode != "fifo_v1" or modes.shadow_mode != "none")


def scheduler_general_settings_changed(changes: dict[str, list[str]]) -> bool:
    touched = (
        set(changes.get("added", ()))
        | set(changes.get("removed", ()))
        | set(changes.get("modified", ()))
    )
    return "general_settings" in touched


def _clear_model_capacity_snapshot_cache(resolver: Any) -> None:
    if resolver is None:
        return
    with contextlib.suppress(AttributeError):
        resolver._snapshot_cache = None
    with contextlib.suppress(AttributeError):
        resolver._snapshot_cache_expires_at = 0.0


def dynamic_config_generation(app: Any) -> int | None:
    dynamic_config_manager = getattr(getattr(app, "state", None), "dynamic_config_manager", None)
    get_config_generation = getattr(dynamic_config_manager, "get_config_generation", None)
    if not callable(get_config_generation):
        return None
    try:
        return int(get_config_generation())
    except Exception:
        logger.debug("batch scheduler config generation lookup failed", exc_info=True)
        return None


def apply_batch_advisory_lock_mode(general: Any) -> None:
    set_advisory_lock_mode(getattr(general, "embeddings_batch_advisory_lock_mode", "dual"))


def _build_model_capacity_resolver(
    *,
    app: Any,
    repository: BatchRepository,
    runtime: BatchRuntime,
    config: BatchModelCapacityConfig,
) -> BatchModelCapacityResolver:
    resolver = runtime.model_capacity_resolver
    if resolver is None:
        return BatchModelCapacityResolver(
            repository=repository,
            config=config,
            router=getattr(repository, "model_group_resolver", None)
            or getattr(app.state, "router", None),
            backpressure=runtime.backpressure,
        )
    resolver.repository = repository
    resolver.config = config
    resolver.router = getattr(repository, "model_group_resolver", None) or getattr(
        app.state, "router", None
    )
    resolver.router_state_backend = None
    resolver.backpressure = runtime.backpressure
    _clear_model_capacity_snapshot_cache(resolver)
    return resolver


def _apply_worker_scheduler_config(
    config: BatchWorkerConfig,
    *,
    general: Any,
    scheduler_modes: BatchSchedulerModes,
    model_capacity_config: BatchModelCapacityConfig,
    tenant_fair_share_config: BatchTenantFairShareConfig,
    size_aging_config: BatchSizeAgingConfig,
) -> None:
    config.scheduler_mode = scheduler_modes.active_mode
    config.scheduler_shadow_mode = scheduler_modes.shadow_mode
    config.scheduler_claim_mode = (
        "work_slice"
        if scheduler_modes.active_uses_work_slice
        else general.embeddings_batch_scheduler_claim_mode
    )
    config.scheduler_shadow_decision_timeout_seconds = (
        general.embeddings_batch_scheduler_shadow_decision_timeout_seconds
    )
    config.scheduler_shadow_max_pending_decisions = (
        general.embeddings_batch_scheduler_shadow_max_pending_decisions
    )
    config.work_claim_max_items = general.embeddings_batch_work_claim_max_items
    config.work_claim_max_work_units = general.embeddings_batch_work_claim_max_work_units
    config.work_claim_min_items_for_microbatch = (
        general.embeddings_batch_work_claim_min_items_for_microbatch
    )
    config.claim_diagnostics_enabled = bool(
        getattr(general, "embeddings_batch_claim_diagnostics_enabled", True)
    )
    config.claim_diagnostic_interval_seconds = getattr(
        general, "embeddings_batch_claim_diagnostic_interval_seconds", 60.0
    )
    config.claim_diagnostic_max_keys = getattr(
        general, "embeddings_batch_claim_diagnostic_max_keys", 1024
    )
    config.model_capacity_enabled = model_capacity_config.enabled
    config.scheduler_shadow_enabled = scheduler_modes.shadow_mode != "none"
    config.tenant_fair_share_enabled = tenant_fair_share_config.enabled
    config.tenant_fair_share_base_quantum_work_units = (
        tenant_fair_share_config.base_quantum_work_units
    )
    config.tenant_fair_share_max_deficit_multiplier = (
        tenant_fair_share_config.max_deficit_multiplier
    )
    config.tenant_max_in_flight_work_units = (
        tenant_fair_share_config.tenant_max_in_flight_work_units
    )
    config.tenant_fair_share_max_active_flows_per_decision = (
        tenant_fair_share_config.max_active_flows_per_decision
    )
    config.tenant_fair_share_max_candidate_jobs_per_flow = (
        tenant_fair_share_config.max_candidate_jobs_per_flow
    )
    config.tenant_fair_share_disabled_model_groups = tenant_fair_share_config.disabled_model_groups
    config.size_aware_scheduling_enabled = size_aging_config.enabled
    config.aging_seconds_per_work_unit = size_aging_config.aging_seconds_per_work_unit
    config.max_age_credit_work_units = size_aging_config.max_age_credit_work_units
    config.min_large_job_claim_interval_seconds = (
        size_aging_config.min_large_job_claim_interval_seconds
    )
    config.small_job_fast_lane_enabled = size_aging_config.small_job_fast_lane_enabled
    config.small_job_max_work_units = size_aging_config.small_job_max_work_units


def _apply_create_session_scheduler_config(
    *,
    runtime: BatchRuntime,
    app: Any,
    general: Any,
    scheduler_modes: BatchSchedulerModes,
    tenant_fair_share_config: BatchTenantFairShareConfig,
) -> None:
    scheduler_enabled = batch_scheduler_active_enabled_for_creation(general)
    scheduler_shadow_enabled = scheduler_modes.shadow_mode != "none"
    strict_model_homogeneity_enabled = getattr(
        general,
        "embeddings_batch_scheduler_strict_model_homogeneity_enabled",
        False,
    )
    default_service_tier = getattr(
        general,
        "embeddings_batch_scheduler_default_service_tier",
        "standard",
    )
    tenant_scope_preference = tenant_fair_share_config.tenant_scope_preference
    tenant_max_queued_work_units = getattr(
        general,
        "embeddings_batch_tenant_max_queued_work_units",
        0,
    )
    if runtime.create_session_promoter is not None:
        runtime.create_session_promoter.configure_scheduler(
            scheduler_enabled=scheduler_enabled,
            scheduler_shadow_enabled=scheduler_shadow_enabled,
            scheduler_mode=scheduler_modes.active_mode,
            scheduler_shadow_mode=scheduler_modes.shadow_mode,
            strict_model_homogeneity_enabled=strict_model_homogeneity_enabled,
            default_service_tier=default_service_tier,
            tenant_scope_preference=tenant_scope_preference,
            tenant_max_queued_work_units=tenant_max_queued_work_units,
        )
    service = getattr(app.state, "batch_create_session_service", None)
    configure_scheduler = getattr(service, "configure_scheduler", None)
    if callable(configure_scheduler):
        configure_scheduler(
            scheduler_enabled=scheduler_enabled,
            scheduler_shadow_enabled=scheduler_shadow_enabled,
            strict_model_homogeneity_enabled=strict_model_homogeneity_enabled,
            default_service_tier=default_service_tier,
        )


def apply_live_batch_scheduler_config(
    *,
    app: Any,
    runtime: BatchRuntime,
    cfg: Any,
    repository: BatchRepository,
) -> BatchSchedulerModes:
    general = cfg.general_settings
    apply_batch_advisory_lock_mode(general)
    scheduler_modes = resolve_scheduler_modes_from_settings(general)
    model_capacity_config = BatchModelCapacityConfig.from_settings(general)
    tenant_fair_share_config = BatchTenantFairShareConfig.from_settings(general)
    size_aging_config = BatchSizeAgingConfig.from_settings(general)
    set_repository_tenant_scope_preference = getattr(
        repository, "set_tenant_scope_preference", None
    )
    if callable(set_repository_tenant_scope_preference):
        set_repository_tenant_scope_preference(tenant_fair_share_config.tenant_scope_preference)

    runtime.model_capacity_resolver = (
        _build_model_capacity_resolver(
            app=app,
            repository=repository,
            runtime=runtime,
            config=model_capacity_config,
        )
        if model_capacity_config.enabled
        else None
    )
    runtime.tenant_fair_share_config = tenant_fair_share_config
    runtime.size_aging_config = size_aging_config
    app.state.app_config = cfg
    app.state.batch_scheduler_modes = scheduler_modes
    app.state.batch_model_capacity_resolver = runtime.model_capacity_resolver
    app.state.batch_tenant_fair_share_config = tenant_fair_share_config
    app.state.batch_size_aging_config = size_aging_config
    worker = runtime.worker
    if worker is not None:
        worker.model_capacity_resolver = runtime.model_capacity_resolver
        _apply_worker_scheduler_config(
            worker.config,
            general=general,
            scheduler_modes=scheduler_modes,
            model_capacity_config=model_capacity_config,
            tenant_fair_share_config=tenant_fair_share_config,
            size_aging_config=size_aging_config,
        )
    _apply_create_session_scheduler_config(
        runtime=runtime,
        app=app,
        general=general,
        scheduler_modes=scheduler_modes,
        tenant_fair_share_config=tenant_fair_share_config,
    )
    if worker is not None:
        mark_scheduler_config_applied = getattr(worker, "mark_scheduler_config_applied", None)
        if callable(mark_scheduler_config_applied):
            mark_scheduler_config_applied(
                general_settings=general,
                config_generation=dynamic_config_generation(app),
            )
    return scheduler_modes


def subscribe_to_batch_scheduler_updates(
    *,
    app: Any,
    runtime: BatchRuntime,
    repository: BatchRepository,
) -> None:
    dynamic_config_manager = getattr(app.state, "dynamic_config_manager", None)
    subscribe = getattr(dynamic_config_manager, "subscribe", None)
    if not callable(subscribe):
        return

    async def _on_batch_config_change(
        new_config: Any,
        changes: dict[str, list[str]],
    ) -> None:
        if not scheduler_general_settings_changed(changes):
            return
        apply_live_batch_scheduler_config(
            app=app,
            runtime=runtime,
            cfg=new_config,
            repository=repository,
        )

    subscribe(_on_batch_config_change)


async def record_startup_scheduler_rollbacks(
    repository: BatchRepository,
    current: BatchSchedulerModes,
) -> None:
    prisma = getattr(repository, "prisma", None)
    if prisma is None:
        return
    payload = json.dumps(
        {
            "active_mode": current.active_mode,
            "shadow_mode": current.shadow_mode,
        },
        sort_keys=True,
    )
    try:
        rows = await prisma.query_raw(
            """
            SELECT config_value
            FROM deltallm_config
            WHERE config_name = $1
            LIMIT 1
            """,
            _BATCH_SCHEDULER_MODE_STATE_KEY,
        )
        if rows:
            previous_payload = rows[0].get("config_value")
            try:
                if isinstance(previous_payload, str):
                    previous_data = json.loads(previous_payload)
                elif isinstance(previous_payload, dict):
                    previous_data = previous_payload
                else:
                    previous_data = {}
            except json.JSONDecodeError:
                previous_data = {}
            previous_modes = BatchSchedulerModes(
                active_mode=normalize_scheduler_mode(previous_data.get("active_mode", "fifo_v1")),
                shadow_mode=normalize_scheduler_shadow_mode(
                    previous_data.get("shadow_mode", "none")
                ),
            )
            for event in scheduler_rollback_events(previous=previous_modes, current=current):
                increment_batch_scheduler_rollback(
                    from_mode=event.from_mode,
                    to_mode=event.to_mode,
                    reason=event.reason,
                )
        await prisma.execute_raw(
            """
            INSERT INTO deltallm_config (config_name, config_value, updated_by, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (config_name) DO UPDATE
            SET config_value = EXCLUDED.config_value,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
            """,
            _BATCH_SCHEDULER_MODE_STATE_KEY,
            payload,
            "batch_runtime",
        )
    except Exception:
        logger.debug("batch scheduler rollback state update failed", exc_info=True)
