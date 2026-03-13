from __future__ import annotations

import asyncio
import contextlib
from asyncio import Task, create_task
from dataclasses import dataclass
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.db.repositories import AuditRepository
from src.services.audit_retention import AuditRetentionConfig, AuditRetentionWorker
from src.services.audit_service import AuditService


@dataclass
class AuditRuntime:
    retention_worker: AuditRetentionWorker | None = None
    retention_task: Task[None] | None = None
    statuses: tuple[BootstrapStatus, ...] = ()


async def init_audit_runtime(app: Any, cfg: Any) -> AuditRuntime:
    app.state.audit_repository = None
    app.state.audit_service = None

    runtime = AuditRuntime(statuses=(BootstrapStatus("audit", "disabled"),))
    if not cfg.general_settings.audit_enabled:
        return runtime

    repository = AuditRepository(app.state.prisma_manager.client)
    service = AuditService(repository)
    await service.start()

    app.state.audit_repository = repository
    app.state.audit_service = service

    if not cfg.general_settings.audit_retention_worker_enabled:
        runtime.statuses = (
            BootstrapStatus("audit", "ready"),
            BootstrapStatus("audit_retention_worker", "disabled"),
        )
        return runtime

    runtime.retention_worker = AuditRetentionWorker(
        repository=repository,
        config=AuditRetentionConfig(
            interval_seconds=cfg.general_settings.audit_retention_interval_seconds,
            scan_limit=cfg.general_settings.audit_retention_scan_limit,
            metadata_retention_days=cfg.general_settings.audit_metadata_retention_days,
            payload_retention_days=cfg.general_settings.audit_payload_retention_days,
        ),
    )
    runtime.retention_task = create_task(runtime.retention_worker.run())
    runtime.statuses = (
        BootstrapStatus("audit", "ready"),
        BootstrapStatus("audit_retention_worker", "ready"),
    )
    return runtime


async def shutdown_audit_runtime(app: Any, runtime: AuditRuntime) -> None:
    if runtime.retention_worker is not None:
        runtime.retention_worker.stop()
    if runtime.retention_task is not None:
        runtime.retention_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.retention_task

    audit_service: AuditService | None = getattr(app.state, "audit_service", None)
    if audit_service is not None:
        await audit_service.shutdown()
