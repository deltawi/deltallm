from __future__ import annotations

from asyncio import CancelledError, Task, create_task
from dataclasses import dataclass
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.db.email_feedback import EmailFeedbackRepository
from src.db.email import EmailOutboxRepository
from src.email.models import EmailConfigurationError
from src.services.email_feedback_service import EmailFeedbackService
from src.services.email_delivery_service import EmailDeliveryService
from src.services.email_outbox_service import EmailOutboxService, EmailOutboxWorker, EmailWorkerConfig


@dataclass
class EmailRuntime:
    worker: EmailOutboxWorker | None = None
    worker_task: Task[None] | None = None
    statuses: tuple[BootstrapStatus, ...] = ()


async def init_email_runtime(app: Any, cfg: Any) -> EmailRuntime:
    repository = getattr(app.state, "email_outbox_repository", None)
    if repository is None:
        repository = EmailOutboxRepository(getattr(getattr(app.state, "prisma_manager", None), "client", None))
        app.state.email_outbox_repository = repository
    feedback_repository = getattr(app.state, "email_feedback_repository", None)
    if feedback_repository is None:
        feedback_repository = EmailFeedbackRepository(getattr(getattr(app.state, "prisma_manager", None), "client", None))
        app.state.email_feedback_repository = feedback_repository

    delivery_service = EmailDeliveryService(
        config_getter=lambda: getattr(app.state, "app_config", cfg),
        http_client=app.state.http_client,
    )
    outbox_service = EmailOutboxService(
        repository=repository,
        delivery_service=delivery_service,
        config_getter=lambda: getattr(app.state, "app_config", cfg),
        feedback_repository=feedback_repository,
    )
    app.state.email_delivery_service = delivery_service
    app.state.email_outbox_service = outbox_service
    app.state.email_feedback_service = EmailFeedbackService(
        repository=feedback_repository,
        config_getter=lambda: getattr(app.state, "app_config", cfg),
    )

    if not bool(getattr(cfg.general_settings, "email_enabled", False)):
        return EmailRuntime(statuses=(BootstrapStatus("email", "disabled"), BootstrapStatus("email_worker", "disabled")))

    try:
        delivery_service.validate_current_config()
    except EmailConfigurationError as exc:
        return EmailRuntime(
            statuses=(
                BootstrapStatus("email", "degraded", str(exc)),
                BootstrapStatus("email_worker", "disabled"),
            )
        )

    runtime = EmailRuntime()
    statuses = [BootstrapStatus("email", "ready")]
    if bool(getattr(cfg.general_settings, "email_worker_enabled", True)):
        runtime.worker = EmailOutboxWorker(
            repository=repository,
            delivery_service=delivery_service,
            config_getter=lambda: getattr(app.state, "app_config", cfg),
            audit_service=getattr(app.state, "audit_service", None),
            feedback_repository=feedback_repository,
            config=EmailWorkerConfig(
                poll_interval_seconds=float(getattr(cfg.general_settings, "email_worker_poll_interval_seconds", 5.0) or 5.0),
                max_batch_size=10,
                max_concurrency=int(getattr(cfg.general_settings, "email_worker_max_concurrency", 3) or 3),
            ),
        )
        runtime.worker_task = create_task(runtime.worker.run())
        statuses.append(BootstrapStatus("email_worker", "ready"))
    else:
        statuses.append(BootstrapStatus("email_worker", "disabled"))
    runtime.statuses = tuple(statuses)
    return runtime


async def shutdown_email_runtime(runtime: EmailRuntime) -> None:
    if runtime.worker is not None:
        runtime.worker.stop()
    if runtime.worker_task is not None:
        runtime.worker_task.cancel()
        try:
            await runtime.worker_task
        except CancelledError:
            pass
