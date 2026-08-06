"""Public bootstrap entry points for the optional batch subsystem.

Implementation details live in ``src.bootstrap.batch_runtime`` so this module
stays a stable, small integration surface for the application bootstrap.
"""

from __future__ import annotations

from typing import Any

from src.batch import BatchRepository
from src.bootstrap.batch_runtime.core import initialize_batch_core
from src.bootstrap.batch_runtime.create_sessions import (
    initialize_create_session_services,
    start_create_session_cleanup,
)
from src.bootstrap.batch_runtime.lifecycle import shutdown_batch_runtime
from src.bootstrap.batch_runtime.runtime import BatchRuntime
from src.bootstrap.batch_runtime.scheduler import (
    apply_batch_advisory_lock_mode as _apply_batch_advisory_lock_mode,
)
from src.bootstrap.batch_runtime.scheduler import subscribe_to_batch_scheduler_updates
from src.bootstrap.batch_runtime.state import (
    build_batch_statuses,
    configure_disabled_batch_state,
)
from src.bootstrap.batch_runtime.workers import start_batch_workers


async def init_batch_runtime(
    app: Any,
    cfg: Any,
    repository: BatchRepository,
) -> BatchRuntime:
    runtime = BatchRuntime()
    app.state.batch_runtime = runtime
    _apply_batch_advisory_lock_mode(cfg.general_settings)

    if not cfg.general_settings.embeddings_batch_enabled:
        configure_disabled_batch_state(app, cfg, runtime)
        return runtime

    core = await initialize_batch_core(app, cfg, repository, runtime)
    webhook_cipher = start_batch_workers(app, cfg, repository, runtime, core)
    initialize_create_session_services(
        app,
        cfg,
        repository,
        runtime,
        core,
        webhook_cipher,
    )
    subscribe_to_batch_scheduler_updates(
        app=app,
        runtime=runtime,
        repository=repository,
    )
    start_create_session_cleanup(
        app,
        cfg,
        runtime,
        core.session_repository,
    )
    runtime.statuses = build_batch_statuses(
        runtime,
        cfg,
        webhook_cipher_configured=webhook_cipher is not None,
    )
    return runtime


__all__ = ["BatchRuntime", "init_batch_runtime", "shutdown_batch_runtime"]
