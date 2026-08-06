from __future__ import annotations

import os
import socket
from asyncio import create_task
from typing import Any
from uuid import uuid4

import httpx

from src.batch import (
    BatchCleanupConfig,
    BatchRepository,
    BatchRetentionCleanupWorker,
    BatchSchedulerBackfillConfig,
    BatchSchedulerBackfillWorker,
    BatchStaleLeaseSweeperConfig,
    BatchStaleLeaseSweeperWorker,
)
from src.batch.completion_outbox import BatchCompletionOutboxWorker, BatchCompletionOutboxWorkerConfig
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.batch.webhooks import BatchWebhookCipher
from src.batch.webhooks.delivery import BatchWebhookHTTPSender
from src.batch.webhooks.network_policy import BatchWebhookNetworkPolicy
from src.batch.webhooks.worker import BatchWebhookOutboxWorker, BatchWebhookOutboxWorkerConfig
from src.bootstrap.batch_runtime.core import BatchCoreComponents
from src.bootstrap.batch_runtime.runtime import BatchRuntime
from src.bootstrap.batch_runtime.scheduler import dynamic_config_generation

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


def start_batch_workers(
    app: Any,
    cfg: Any,
    repository: BatchRepository,
    runtime: BatchRuntime,
    core: BatchCoreComponents,
) -> BatchWebhookCipher | None:
    _start_executor_worker(app, cfg, repository, runtime, core)
    _start_completion_outbox_worker(app, cfg, repository, runtime)
    webhook_cipher = _start_webhook_worker(app, cfg, repository, runtime)
    _start_maintenance_workers(app, cfg, repository, runtime, core)
    return webhook_cipher


def _start_executor_worker(
    app: Any,
    cfg: Any,
    repository: BatchRepository,
    runtime: BatchRuntime,
    core: BatchCoreComponents,
) -> None:
    general = cfg.general_settings
    if not general.embeddings_batch_worker_enabled:
        return

    worker_kwargs = {
        "app": app,
        "repository": repository,
        "storage": core.storage,
        "config": BatchWorkerConfig(
            worker_id=_batch_worker_id("batch-executor"),
            poll_interval_seconds=general.embeddings_batch_poll_interval_seconds,
            heartbeat_interval_seconds=general.embeddings_batch_heartbeat_interval_seconds,
            job_lease_seconds=general.embeddings_batch_job_lease_seconds,
            item_lease_seconds=general.embeddings_batch_item_lease_seconds,
            finalization_retry_delay_seconds=general.embeddings_batch_finalization_retry_delay_seconds,
            worker_concurrency=general.embeddings_batch_worker_concurrency,
            item_buffer_multiplier=general.embeddings_batch_item_buffer_multiplier,
            finalization_page_size=general.embeddings_batch_finalization_page_size,
            item_claim_limit=general.embeddings_batch_item_claim_limit,
            scheduler_mode=core.scheduler_modes.active_mode,
            scheduler_shadow_mode=core.scheduler_modes.shadow_mode,
            scheduler_claim_mode=(
                "work_slice"
                if core.scheduler_modes.active_uses_work_slice
                else general.embeddings_batch_scheduler_claim_mode
            ),
            scheduler_shadow_decision_timeout_seconds=(
                general.embeddings_batch_scheduler_shadow_decision_timeout_seconds
            ),
            scheduler_shadow_max_pending_decisions=(
                general.embeddings_batch_scheduler_shadow_max_pending_decisions
            ),
            work_claim_max_items=general.embeddings_batch_work_claim_max_items,
            work_claim_max_work_units=general.embeddings_batch_work_claim_max_work_units,
            work_claim_min_items_for_microbatch=(
                general.embeddings_batch_work_claim_min_items_for_microbatch
            ),
            claim_diagnostics_enabled=getattr(
                general,
                "embeddings_batch_claim_diagnostics_enabled",
                True,
            ),
            claim_diagnostic_interval_seconds=getattr(
                general,
                "embeddings_batch_claim_diagnostic_interval_seconds",
                60.0,
            ),
            claim_diagnostic_max_keys=getattr(
                general,
                "embeddings_batch_claim_diagnostic_max_keys",
                1024,
            ),
            model_capacity_enabled=core.model_capacity_config.enabled,
            scheduler_shadow_enabled=core.scheduler_modes.shadow_mode != "none",
            tenant_fair_share_enabled=core.tenant_fair_share_config.enabled,
            tenant_fair_share_base_quantum_work_units=(
                core.tenant_fair_share_config.base_quantum_work_units
            ),
            tenant_fair_share_max_deficit_multiplier=(
                core.tenant_fair_share_config.max_deficit_multiplier
            ),
            tenant_max_in_flight_work_units=(
                core.tenant_fair_share_config.tenant_max_in_flight_work_units
            ),
            tenant_fair_share_max_active_flows_per_decision=(
                core.tenant_fair_share_config.max_active_flows_per_decision
            ),
            tenant_fair_share_max_candidate_jobs_per_flow=(
                core.tenant_fair_share_config.max_candidate_jobs_per_flow
            ),
            tenant_fair_share_disabled_model_groups=(
                core.tenant_fair_share_config.disabled_model_groups
            ),
            size_aware_scheduling_enabled=core.size_aging_config.enabled,
            aging_seconds_per_work_unit=core.size_aging_config.aging_seconds_per_work_unit,
            max_age_credit_work_units=core.size_aging_config.max_age_credit_work_units,
            min_large_job_claim_interval_seconds=(
                core.size_aging_config.min_large_job_claim_interval_seconds
            ),
            small_job_fast_lane_enabled=core.size_aging_config.small_job_fast_lane_enabled,
            small_job_max_work_units=core.size_aging_config.small_job_max_work_units,
            finalization_first=general.embeddings_batch_finalization_first,
            max_attempts=general.embeddings_batch_max_attempts,
            retry_initial_seconds=general.embeddings_batch_retry_initial_seconds,
            retry_max_seconds=general.embeddings_batch_retry_max_seconds,
            retry_multiplier=general.embeddings_batch_retry_multiplier,
            retry_jitter=general.embeddings_batch_retry_jitter,
            microbatch_retry_enabled=general.embeddings_batch_microbatch_retry_enabled,
            microbatch_max_group_retries=general.embeddings_batch_microbatch_max_group_retries,
            microbatch_min_reduced_size=general.embeddings_batch_microbatch_min_reduced_size,
            microbatch_reduce_factor=general.embeddings_batch_microbatch_reduce_factor,
            completed_artifact_retention_days=general.batch_completed_artifact_retention_days,
            failed_artifact_retention_days=general.batch_failed_artifact_retention_days,
        ),
    }
    if runtime.model_capacity_resolver is not None:
        worker_kwargs["model_capacity_resolver"] = runtime.model_capacity_resolver
    runtime.worker = BatchExecutorWorker(**worker_kwargs)
    mark_scheduler_config_applied = getattr(runtime.worker, "mark_scheduler_config_applied", None)
    if callable(mark_scheduler_config_applied):
        mark_scheduler_config_applied(
            general_settings=general,
            config_generation=dynamic_config_generation(app),
        )
    runtime.worker_task = create_task(runtime.worker.run())


def _start_completion_outbox_worker(
    app: Any,
    cfg: Any,
    repository: BatchRepository,
    runtime: BatchRuntime,
) -> None:
    general = cfg.general_settings
    if not general.embeddings_batch_completion_outbox_worker_enabled:
        return

    runtime.completion_outbox_worker = BatchCompletionOutboxWorker(
        app=app,
        repository=repository,
        config=BatchCompletionOutboxWorkerConfig(
            worker_id=_batch_worker_id("batch-completion-outbox"),
            poll_interval_seconds=general.embeddings_batch_poll_interval_seconds,
            max_batch_size=max(10, int(general.embeddings_batch_item_claim_limit or 20)),
            max_concurrency=min(
                8,
                max(1, int(general.embeddings_batch_worker_concurrency or 4)),
            ),
            lease_seconds=max(15, int(general.embeddings_batch_item_lease_seconds or 60)),
            heartbeat_interval_seconds=max(
                1.0,
                float(general.embeddings_batch_heartbeat_interval_seconds or 10.0),
            ),
        ),
    )
    runtime.completion_outbox_task = create_task(runtime.completion_outbox_worker.run())


def _start_webhook_worker(
    app: Any,
    cfg: Any,
    repository: BatchRepository,
    runtime: BatchRuntime,
) -> BatchWebhookCipher | None:
    general = cfg.general_settings
    encryption_key = getattr(general, "batch_webhook_encryption_key", None)
    cipher = BatchWebhookCipher.from_config(encryption_key) if encryption_key is not None else None
    if not bool(getattr(general, "batch_webhook_worker_enabled", True)) or cipher is None:
        return cipher

    webhook_concurrency = max(1, int(getattr(general, "batch_webhook_max_concurrency", 4)))
    runtime.webhook_transport = httpx.AsyncHTTPTransport(
        retries=0,
        trust_env=False,
        limits=httpx.Limits(
            max_connections=webhook_concurrency,
            max_keepalive_connections=0,
        ),
    )
    runtime.webhook_outbox_worker = BatchWebhookOutboxWorker(
        repository=repository,
        cipher=cipher,
        network_policy=BatchWebhookNetworkPolicy(
            allow_http=bool(getattr(general, "batch_webhook_allow_http", False)),
            allowed_ports=getattr(general, "batch_webhook_allowed_ports", [443]),
            allowed_private_cidrs=getattr(general, "batch_webhook_allowed_private_cidrs", []),
            resolution_timeout_seconds=float(
                getattr(general, "batch_webhook_timeout_seconds", 10.0)
            ),
        ),
        sender=BatchWebhookHTTPSender(
            transport=runtime.webhook_transport,
            timeout_seconds=float(getattr(general, "batch_webhook_timeout_seconds", 10.0)),
        ),
        config=BatchWebhookOutboxWorkerConfig(
            worker_id=_batch_worker_id("batch-webhook-outbox"),
            poll_interval_seconds=float(
                getattr(general, "batch_webhook_poll_interval_seconds", 1.0)
            ),
            max_concurrency=webhook_concurrency,
            lease_seconds=int(getattr(general, "batch_webhook_lease_seconds", 30)),
            retry_initial_seconds=int(
                getattr(general, "batch_webhook_retry_initial_seconds", 5)
            ),
            retry_max_seconds=int(
                getattr(general, "batch_webhook_retry_max_seconds", 3_600)
            ),
        ),
    )
    runtime.webhook_outbox_task = create_task(runtime.webhook_outbox_worker.run())
    app.state.batch_webhook_outbox_worker = runtime.webhook_outbox_worker
    app.state.batch_webhook_outbox_task = runtime.webhook_outbox_task
    app.state.batch_webhook_worker_expected = True
    return cipher


def _start_maintenance_workers(
    app: Any,
    cfg: Any,
    repository: BatchRepository,
    runtime: BatchRuntime,
    core: BatchCoreComponents,
) -> None:
    general = cfg.general_settings
    if general.embeddings_batch_gc_enabled:
        runtime.gc_worker = BatchRetentionCleanupWorker(
            repository=repository,
            storage=core.storage,
            storage_registry=core.storage_registry,
            config=BatchCleanupConfig(
                interval_seconds=general.embeddings_batch_gc_interval_seconds,
                scan_limit=general.embeddings_batch_gc_scan_limit,
            ),
        )
        runtime.gc_task = create_task(runtime.gc_worker.run())

    if getattr(general, "embeddings_batch_scheduler_backfill_enabled", False):
        runtime.scheduler_backfill_worker = BatchSchedulerBackfillWorker(
            repository=repository,
            config=BatchSchedulerBackfillConfig(
                interval_seconds=getattr(
                    general,
                    "embeddings_batch_scheduler_backfill_interval_seconds",
                    60.0,
                ),
                scan_limit=getattr(
                    general,
                    "embeddings_batch_scheduler_backfill_scan_limit",
                    500,
                ),
            ),
        )
        app.state.batch_scheduler_backfill_worker = runtime.scheduler_backfill_worker
        runtime.scheduler_backfill_task = create_task(runtime.scheduler_backfill_worker.run())

    if getattr(general, "embeddings_batch_stale_lease_sweeper_enabled", True):
        runtime.stale_lease_sweeper_worker = BatchStaleLeaseSweeperWorker(
            repository=repository,
            config=BatchStaleLeaseSweeperConfig(
                interval_seconds=getattr(
                    general,
                    "embeddings_batch_stale_lease_sweeper_interval_seconds",
                    60.0,
                ),
                failure_interval_seconds=getattr(
                    general,
                    "embeddings_batch_stale_lease_sweeper_failure_interval_seconds",
                    30.0,
                ),
                page_size=getattr(
                    general,
                    "embeddings_batch_stale_lease_sweeper_page_size",
                    100,
                ),
                max_rows_per_run=getattr(
                    general,
                    "embeddings_batch_stale_lease_sweeper_max_rows_per_run",
                    500,
                ),
            ),
        )
        app.state.batch_stale_lease_sweeper_worker = runtime.stale_lease_sweeper_worker
        runtime.stale_lease_sweeper_task = create_task(runtime.stale_lease_sweeper_worker.run())
