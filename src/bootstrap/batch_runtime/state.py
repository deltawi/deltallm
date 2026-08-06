from __future__ import annotations

from typing import Any

from src.batch.scheduling import resolve_scheduler_modes_from_settings
from src.bootstrap.batch_runtime.runtime import BatchRuntime
from src.bootstrap.status import BootstrapStatus


def configure_disabled_batch_state(app: Any, cfg: Any, runtime: BatchRuntime) -> None:
    app.state.batch_storage = None
    app.state.batch_storage_registry = None
    app.state.batch_service = None
    app.state.batch_create_session_repository = None
    _initialize_optional_state(app)
    app.state.batch_backpressure = None
    app.state.batch_model_capacity_resolver = None
    app.state.batch_tenant_fair_share_config = None
    app.state.batch_size_aging_config = None
    app.state.batch_scheduler_modes = resolve_scheduler_modes_from_settings(cfg.general_settings)
    runtime.statuses = (BootstrapStatus("embeddings_batch", "disabled"),)


def configure_enabled_batch_state(app: Any, repository: Any) -> None:
    app.state.batch_create_session_repository = getattr(repository, "create_sessions", None)
    _initialize_optional_state(app)


def _initialize_optional_state(app: Any) -> None:
    app.state.batch_create_staging_backend = None
    app.state.batch_create_promoter = None
    app.state.batch_create_session_service = None
    app.state.batch_create_session_admin_service = None
    app.state.batch_create_session_cleanup_worker = None
    app.state.batch_scheduler_backfill_worker = None
    app.state.batch_stale_lease_sweeper_worker = None
    app.state.batch_webhook_outbox_worker = None
    app.state.batch_webhook_outbox_task = None
    app.state.batch_webhook_worker_expected = False


def build_batch_statuses(
    runtime: BatchRuntime,
    cfg: Any,
    *,
    webhook_cipher_configured: bool,
) -> tuple[BootstrapStatus, ...]:
    return (
        BootstrapStatus("embeddings_batch", "ready"),
        BootstrapStatus(
            "embeddings_batch_worker",
            "ready" if runtime.worker is not None else "disabled",
        ),
        BootstrapStatus(
            "embeddings_batch_completion_outbox",
            "ready" if runtime.completion_outbox_worker is not None else "disabled",
        ),
        BootstrapStatus(
            "batch_webhook_outbox",
            "ready" if runtime.webhook_outbox_worker is not None else "disabled",
            (
                "encryption key not configured"
                if bool(getattr(cfg.general_settings, "batch_webhook_worker_enabled", True))
                and not webhook_cipher_configured
                else None
            ),
        ),
        BootstrapStatus(
            "embeddings_batch_gc",
            "ready" if runtime.gc_worker is not None else "disabled",
        ),
        BootstrapStatus(
            "embeddings_batch_scheduler_backfill",
            "ready" if runtime.scheduler_backfill_worker is not None else "disabled",
        ),
        BootstrapStatus(
            "embeddings_batch_stale_lease_sweeper",
            "ready" if runtime.stale_lease_sweeper_worker is not None else "disabled",
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
