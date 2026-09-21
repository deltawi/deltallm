from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from src.bootstrap import (
    BootstrapStatus,
    format_bootstrap_summary,
    init_audit_runtime,
    init_auth_runtime,
    init_batch_runtime,
    init_email_runtime,
    init_infrastructure_runtime,
    init_runtime_services,
    init_routing_runtime,
    shutdown_audit_runtime,
    shutdown_auth_runtime,
    shutdown_batch_runtime,
    shutdown_email_runtime,
    shutdown_infrastructure_runtime,
    shutdown_runtime_services,
    shutdown_routing_runtime,
)
from src.bootstrap.metrics import start_runtime_metrics
from src.bootstrap.lifecycle import process_scope, mark_process_serving, shutdown_readiness
from src.shutdown import BoundedExitStack
from src.startup_config import StartupConfig
from src.process_lifecycle import ProcessLifecycle
from src.rate_limit_release_retry import get_rate_limit_release_retry_queue
from src.cache import (
    CacheMiddleware,
)
from src.api.admin import admin_router
from src.middleware.rate_limit_headers import RateLimitHeaderMiddleware
from src.middleware.rate_limit_lifecycle import RateLimitLeaseLifecycleMiddleware
from src.middleware.ingress import IngressMiddleware
from src.middleware.request_deadline import RequestDeadlineMiddleware
from src.ingress import initialize_ingress
from src.middleware.request_timing import RequestTimingMiddleware
from src.api.v1.router import v1_router
from src.middleware.errors import register_exception_handlers
from src.middleware.platform_auth import attach_platform_auth_context
from src.ui.routes import mount_ui_bundle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _collect_startup_statuses(*groups: tuple[BootstrapStatus, ...]) -> tuple[BootstrapStatus, ...]:
    merged: list[BootstrapStatus] = []
    for group in groups:
        merged.extend(group)
    return tuple(merged)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with process_scope(app) as lifecycle, BoundedExitStack() as exit_stack:
        infrastructure_runtime = await init_infrastructure_runtime(app)
        exit_stack.push_async_callback(
            shutdown_infrastructure_runtime, infrastructure_runtime, cleanup_phase="close"
        )

        cfg = app.state.app_config
        initialize_ingress(app, cfg.general_settings, app.state.settings)
        exit_stack.push_async_callback(get_rate_limit_release_retry_queue(app).stop)
        audit_runtime = await init_audit_runtime(app, cfg)
        exit_stack.push_async_callback(shutdown_audit_runtime, app, audit_runtime)

        email_runtime = await init_email_runtime(app, cfg)
        exit_stack.push_async_callback(shutdown_email_runtime, email_runtime)

        auth_runtime = await init_auth_runtime(app, cfg)
        exit_stack.push_async_callback(shutdown_auth_runtime, auth_runtime)

        routing_runtime = await init_routing_runtime(
            app,
            cfg=cfg,
            settings=app.state.settings,
            dynamic_config_manager=app.state.dynamic_config_manager,
            redis_client=app.state.redis,
            salt_key=app.state.salt_key,
        )
        exit_stack.push_async_callback(shutdown_routing_runtime, routing_runtime)

        runtime_services = await init_runtime_services(app, cfg)
        exit_stack.push_async_callback(shutdown_runtime_services, runtime_services)

        batch_runtime = await init_batch_runtime(app, cfg, app.state.batch_repository)
        exit_stack.push_async_callback(shutdown_batch_runtime, batch_runtime)

        runtime_metrics = start_runtime_metrics()
        exit_stack.callback(runtime_metrics.close)

        startup_statuses = _collect_startup_statuses(
            infrastructure_runtime.statuses,
            audit_runtime.statuses,
            email_runtime.statuses,
            auth_runtime.statuses,
            routing_runtime.statuses,
            runtime_services.statuses,
            batch_runtime.statuses,
        )
        logger.info(format_bootstrap_summary("startup", startup_statuses))
        mark_process_serving(app, lifecycle)
        exit_stack.push_async_callback(shutdown_readiness, app, lifecycle)
        logger.info("application startup complete")
        yield


def create_app(
    *, startup: StartupConfig | None = None, lifecycle: ProcessLifecycle | None = None
) -> FastAPI:
    app = FastAPI(title="DeltaLLM Core API", version="0.1.0", lifespan=lifespan)
    if startup is not None:
        app.state.startup_config = startup
    if lifecycle is not None:
        app.state.process_lifecycle = lifecycle
    register_exception_handlers(app)
    app.add_middleware(CacheMiddleware)
    app.add_middleware(RateLimitHeaderMiddleware)

    @app.middleware("http")
    async def _platform_auth_context_middleware(request: Request, call_next):
        await attach_platform_auth_context(request)
        return await call_next(request)

    # This must wrap cache and route middleware so streaming rate-limit leases
    # remain owned until the final response body frame or a disconnect.
    app.add_middleware(RateLimitLeaseLifecycleMiddleware)
    app.add_middleware(IngressMiddleware)
    app.add_middleware(RequestDeadlineMiddleware)
    app.add_middleware(RequestTimingMiddleware)

    app.include_router(v1_router)
    app.include_router(admin_router)

    mount_ui_bundle(app)
    return app


app = create_app()
