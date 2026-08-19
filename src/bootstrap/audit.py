from __future__ import annotations

import asyncio
import contextlib
from asyncio import Task, create_task
from dataclasses import dataclass
import os
import socket
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.db.repositories import AuditRepository
from src.db.prompt_registry import PromptRegistryRepository
from src.redis_namespace import build_redis_channel
from src.services.audit_retention import AuditRetentionConfig, AuditRetentionWorker
from src.services.audit_service import AuditIngestionConfig, AuditService


@dataclass
class AuditRuntime:
    retention_worker: AuditRetentionWorker | None = None
    retention_task: Task[None] | None = None
    statuses: tuple[BootstrapStatus, ...] = ()


def _startup_setting(general_settings: Any, settings: Any, field_name: str, default: Any) -> Any:
    fields_set = getattr(general_settings, "model_fields_set", None)
    if fields_set is None or field_name in fields_set:
        value = getattr(general_settings, field_name, None)
        if value is not None:
            return value
    return getattr(settings, field_name, default)


async def init_audit_runtime(app: Any, cfg: Any) -> AuditRuntime:
    app.state.audit_repository = None
    app.state.audit_service = None

    runtime = AuditRuntime(statuses=(BootstrapStatus("audit", "disabled"),))
    if not cfg.general_settings.audit_enabled:
        return runtime

    settings = getattr(app.state, "settings", None)
    ingestion_mode = str(
        getattr(app.state, "audit_ingestion_mode", None)
        or _startup_setting(cfg.general_settings, settings, "audit_ingestion_mode", "legacy")
        or "legacy"
    )
    telemetry_client = getattr(
        getattr(app.state, "telemetry_prisma_manager", None),
        "client",
        None,
    )
    if ingestion_mode == "outbox" and telemetry_client is None:
        raise RuntimeError("audit outbox mode requires the dedicated telemetry database pool")
    audit_db_client = (
        telemetry_client if ingestion_mode == "outbox" else app.state.prisma_manager.client
    )
    repository = AuditRepository(audit_db_client)
    service = AuditService(
        repository,
        db_client=audit_db_client,
        prompt_repository=PromptRegistryRepository(audit_db_client),
        redis_client=getattr(app.state, "redis", None),
        policy_invalidation_channel=build_redis_channel(
            application=str(getattr(cfg.general_settings, "instance_name", "deltallm")),
            environment=str(getattr(settings, "app_env", "dev")),
            schema_version=1,
            capability="audit-content-policy-invalidation",
        ),
        ingestion_config=AuditIngestionConfig(
            enabled=ingestion_mode == "outbox",
            worker_enabled=bool(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_worker_enabled", True
                )
            ),
            batch_size=int(
                _startup_setting(cfg.general_settings, settings, "audit_ingestion_batch_size", 100)
            ),
            flush_interval_seconds=float(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_flush_interval_ms", 100
                )
            )
            / 1000.0,
            lease_seconds=int(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_lease_seconds", 30
                )
            ),
            max_attempts=int(
                _startup_setting(cfg.general_settings, settings, "audit_ingestion_max_attempts", 10)
            ),
            max_pending_events=int(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_max_pending_events", 100_000
                )
            ),
            required_reserve=int(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_required_reserve", 10_000
                )
            ),
            completed_retention_hours=int(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_completed_retention_hours", 1
                )
            ),
            failed_retention_days=int(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_failed_retention_days", 30
                )
            ),
            cleanup_interval_seconds=float(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_cleanup_interval_seconds", 60.0
                )
            ),
            cleanup_batch_size=int(
                _startup_setting(
                    cfg.general_settings, settings, "audit_ingestion_cleanup_batch_size", 1000
                )
            ),
            cleanup_max_batches_per_run=int(
                _startup_setting(
                    cfg.general_settings,
                    settings,
                    "audit_ingestion_cleanup_max_batches_per_run",
                    10,
                )
            ),
            cleanup_time_budget_seconds=float(
                _startup_setting(
                    cfg.general_settings,
                    settings,
                    "audit_ingestion_cleanup_time_budget_seconds",
                    2.0,
                )
            ),
            worker_startup_timeout_seconds=float(
                _startup_setting(
                    cfg.general_settings,
                    settings,
                    "telemetry_worker_startup_timeout_seconds",
                    5.0,
                )
            ),
            shutdown_drain_timeout_seconds=float(
                _startup_setting(
                    cfg.general_settings,
                    settings,
                    "telemetry_shutdown_drain_timeout_seconds",
                    20.0,
                )
            ),
            worker_id=f"{socket.gethostname()}:{os.getpid()}:audit",
        ),
    )
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
