"""Application wiring for the launcher-owned process lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from src.bootstrap.readiness import initialize_readiness
from src.process_lifecycle import ProcessLifecycle
from src.shutdown import ShutdownOwner, shutdown_owner
from src.startup_config import StartupConfig


@asynccontextmanager
async def process_scope(app: Any):
    startup = getattr(app.state, "startup_config", None) or StartupConfig.load()
    app.state.startup_config = startup
    lifecycle = getattr(app.state, "process_lifecycle", None) or ProcessLifecycle(startup.lifecycle)
    app.state.process_lifecycle = lifecycle
    owner = ShutdownOwner(lifecycle)
    app.state.shutdown_owner = owner
    token = shutdown_owner.set(owner)
    try:
        yield lifecycle
    finally:
        lifecycle.mark_stopped()
        shutdown_owner.reset(token)


def mark_process_serving(app: Any, lifecycle: ProcessLifecycle) -> None:
    app.state.dynamic_config_manager.activate_updates()
    initialize_readiness(app, app.state.app_config, lifecycle)
    lifecycle.mark_serving()


async def shutdown_readiness(app: Any, lifecycle: ProcessLifecycle) -> None:
    runtime = getattr(app.state, "readiness_runtime", None)
    if runtime is not None:
        await runtime.close(deadline=lifecycle.begin_stopping().cancellation)
