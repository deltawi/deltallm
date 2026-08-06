from __future__ import annotations

from asyncio import Task
from dataclasses import dataclass

import httpx

from src.batch import (
    BatchRetentionCleanupWorker,
    BatchSchedulerBackfillWorker,
    BatchStaleLeaseSweeperWorker,
)
from src.batch.backpressure import BatchBackpressureCoordinator
from src.batch.create.admin_service import BatchCreateSessionAdminService
from src.batch.create.cleanup import BatchCreateSessionCleanupWorker
from src.batch.create.promoter import BatchCreateSessionPromoter
from src.batch.create.staging import BatchCreateArtifactStorageBackend
from src.batch.completion_outbox import BatchCompletionOutboxWorker
from src.batch.scheduling import (
    BatchModelCapacityResolver,
    BatchSizeAgingConfig,
    BatchTenantFairShareConfig,
)
from src.batch.worker import BatchExecutorWorker
from src.batch.webhooks.worker import BatchWebhookOutboxWorker
from src.bootstrap.status import BootstrapStatus


@dataclass
class BatchRuntime:
    """Resources owned by the optional batch subsystem."""

    backpressure: BatchBackpressureCoordinator | None = None
    model_capacity_resolver: BatchModelCapacityResolver | None = None
    tenant_fair_share_config: BatchTenantFairShareConfig | None = None
    size_aging_config: BatchSizeAgingConfig | None = None
    worker: BatchExecutorWorker | None = None
    worker_task: Task[None] | None = None
    completion_outbox_worker: BatchCompletionOutboxWorker | None = None
    completion_outbox_task: Task[None] | None = None
    webhook_outbox_worker: BatchWebhookOutboxWorker | None = None
    webhook_outbox_task: Task[None] | None = None
    webhook_transport: httpx.AsyncBaseTransport | None = None
    gc_worker: BatchRetentionCleanupWorker | None = None
    gc_task: Task[None] | None = None
    create_session_staging_backend: BatchCreateArtifactStorageBackend | None = None
    create_session_promoter: BatchCreateSessionPromoter | None = None
    create_session_admin_service: BatchCreateSessionAdminService | None = None
    create_session_cleanup_worker: BatchCreateSessionCleanupWorker | None = None
    create_session_cleanup_task: Task[None] | None = None
    scheduler_backfill_worker: BatchSchedulerBackfillWorker | None = None
    scheduler_backfill_task: Task[None] | None = None
    stale_lease_sweeper_worker: BatchStaleLeaseSweeperWorker | None = None
    stale_lease_sweeper_task: Task[None] | None = None
    statuses: tuple[BootstrapStatus, ...] = ()
