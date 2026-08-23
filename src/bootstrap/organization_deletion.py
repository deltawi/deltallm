from __future__ import annotations

from asyncio import Task, create_task
import logging
import os
import socket
from typing import Any
from uuid import uuid4

from src.bootstrap.status import BootstrapStatus
from src.db.organization_deletion_cleanup_repository import (
    OrganizationDeletionCleanupRepository,
)
from src.db.organization_deletion_repository import OrganizationDeletionRepository
from src.db.organization_deletion_worker_repository import (
    OrganizationDeletionWorkerRepository,
)
from src.organization_deletion_migrations import verify_readiness
from src.services.organization_deletion import OrganizationDeletionService
from src.services.organization_deletion_worker import (
    OrganizationDeletionWorker,
    OrganizationDeletionWorkerConfig,
)
from src.services.organization_lifecycle import OrganizationLifecycleAuthorizer

logger = logging.getLogger(__name__)
_WORKER_BOOT_ID = uuid4().hex[:12]


async def require_organization_deletion_readiness(
    prisma_client: Any,
    *,
    requests_enabled: bool,
) -> None:
    if not requests_enabled:
        return
    report = await verify_readiness(prisma_client)
    if report.get("ready") is not True:
        raise RuntimeError(
            "organization deletion database readiness is incomplete; "
            "keep organization_deletion_requests_enabled false"
        )


def initialize_organization_lifecycle(app: Any, cfg: Any) -> OrganizationDeletionRepository:
    repository = OrganizationDeletionRepository(app.state.prisma_manager.client)
    app.state.organization_lifecycle_authorizer = OrganizationLifecycleAuthorizer(
        repository,
        max_staleness_seconds=float(
            getattr(
                cfg.general_settings,
                "organization_lifecycle_auth_max_staleness_seconds",
                3.0,
            )
            or 3.0
        ),
        max_entries=int(
            getattr(
                cfg.general_settings,
                "organization_lifecycle_auth_cache_max_entries",
                10_000,
            )
            or 10_000
        ),
    )
    return repository


def initialize_organization_deletion_runtime(
    app: Any,
    cfg: Any,
    runtime: Any,
    repository: OrganizationDeletionRepository,
    statuses: list[BootstrapStatus],
) -> None:
    app.state.organization_deletion_service = OrganizationDeletionService(
        repository=repository,
        cache_invalidation_service=app.state.cache_invalidation_service,
        lifecycle_authorizer=app.state.organization_lifecycle_authorizer,
        recovery_window_hours=int(
            getattr(cfg.general_settings, "organization_deletion_recovery_window_hours", 168) or 168
        ),
        max_attempts=int(
            getattr(cfg.general_settings, "organization_deletion_max_attempts", 20) or 20
        ),
        requests_enabled=bool(
            getattr(
                cfg.general_settings,
                "organization_deletion_requests_enabled",
                False,
            )
        ),
    )
    statuses.extend(
        (
            BootstrapStatus("organization_lifecycle_authorizer", "ready"),
            BootstrapStatus("organization_deletion_service", "ready"),
        )
    )
    if not bool(getattr(cfg.general_settings, "organization_deletion_worker_enabled", True)):
        app.state.organization_deletion_worker = None
        app.state.organization_deletion_worker_expected = False
        app.state.organization_deletion_task = None
        statuses.append(BootstrapStatus("organization_deletion_worker", "disabled"))
        return

    runtime.organization_deletion_worker = OrganizationDeletionWorker(
        repository=OrganizationDeletionWorkerRepository(app.state.prisma_manager.client),
        cleanup_repository=OrganizationDeletionCleanupRepository(app.state.prisma_manager.client),
        worker_id=_organization_deletion_worker_id(),
        config=_worker_config(cfg.general_settings),
        lifecycle_authorizer=app.state.organization_lifecycle_authorizer,
    )
    app.state.organization_deletion_worker = runtime.organization_deletion_worker
    app.state.organization_deletion_worker_expected = True
    statuses.append(BootstrapStatus("organization_deletion_worker", "ready"))


def start_organization_deletion_tasks(app: Any, runtime: Any) -> None:
    runtime.organization_lifecycle_task = create_task(
        app.state.organization_lifecycle_authorizer.run()
    )
    app.state.organization_lifecycle_refresher_expected = True
    app.state.organization_lifecycle_task = runtime.organization_lifecycle_task
    runtime.organization_lifecycle_task.add_done_callback(
        lambda task: _report_background_task_exit("organization lifecycle refresher", task)
    )
    if runtime.organization_deletion_worker is not None:
        runtime.organization_deletion_task = create_task(runtime.organization_deletion_worker.run())
        app.state.organization_deletion_task = runtime.organization_deletion_task
        runtime.organization_deletion_task.add_done_callback(
            lambda task: _report_background_task_exit("organization deletion worker", task)
        )


def _worker_config(general_settings: Any) -> OrganizationDeletionWorkerConfig:
    lease_seconds = int(
        getattr(general_settings, "organization_deletion_worker_lease_seconds", 60) or 60
    )
    configured_timeout = float(
        getattr(
            general_settings,
            "organization_deletion_worker_record_timeout_seconds",
            45.0,
        )
        or 45.0
    )
    return OrganizationDeletionWorkerConfig(
        poll_interval_seconds=float(
            getattr(general_settings, "organization_deletion_worker_poll_interval_seconds", 5.0)
            or 5.0
        ),
        max_batch_size=int(
            getattr(general_settings, "organization_deletion_worker_batch_size", 5) or 5
        ),
        max_concurrency=int(
            getattr(general_settings, "organization_deletion_worker_max_concurrency", 2) or 2
        ),
        lease_seconds=lease_seconds,
        record_timeout_seconds=min(
            max(0.001, configured_timeout),
            max(0.001, float(lease_seconds) - 0.5),
        ),
        page_size=int(
            getattr(general_settings, "organization_deletion_worker_page_size", 100) or 100
        ),
        max_pages_per_claim=int(
            getattr(general_settings, "organization_deletion_worker_max_pages_per_claim", 10) or 10
        ),
        waiting_poll_seconds=float(
            getattr(general_settings, "organization_deletion_waiting_poll_seconds", 10.0) or 10.0
        ),
        retry_initial_seconds=int(
            getattr(general_settings, "organization_deletion_retry_initial_seconds", 5) or 5
        ),
        retry_max_seconds=int(
            getattr(general_settings, "organization_deletion_retry_max_seconds", 300) or 300
        ),
    )


def _organization_deletion_worker_id() -> str:
    host = "".join(
        char if char.isascii() and (char.isalnum() or char in {"-", "_", "."}) else "-"
        for char in str(socket.gethostname() or "").strip()
    ).strip("-._")
    return f"organization-deletion-{host or 'unknown-host'}-{os.getpid()}-{_WORKER_BOOT_ID}"


def _report_background_task_exit(label: str, task: Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is None:
        logger.error("%s exited unexpectedly", label)
        return
    logger.error(
        "%s crashed",
        label,
        exc_info=(type(error), error, error.__traceback__),
    )


__all__ = [
    "initialize_organization_deletion_runtime",
    "initialize_organization_lifecycle",
    "require_organization_deletion_readiness",
    "start_organization_deletion_tasks",
]
