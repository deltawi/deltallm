"""Minimal accounting role apps use the existing launcher and shutdown owner."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import JSONResponse, Response

from src.accounting_settings import AccountingProtocolSettings
from src.api.internal_accounting import accounting_rpc_router
from src.bootstrap.accounting_role_builders import (
    build_accounting_projection_runtime,
    build_accounting_request_runtime,
)
from src.bootstrap.accounting_roles import AccountingProjectionRuntime, AccountingRequestRuntime
from src.config import DatabaseConnectionSettings
from src.db.accounting_pool import AccountingPostgresManager
from src.db.migration_status import verify_migration_status
from src.ingress import IngressLimits, IngressRuntime
from src.metrics.prometheus import get_prometheus_registry
from src.middleware.ingress import IngressMiddleware
from src.process_lifecycle import ProcessLifecycle
from src.shutdown import BoundedExitStack, ShutdownOwner, cleanup_deadline, shutdown_owner
from src.telemetry.lifecycle import WorkerState


AccountingWorkerRole = Literal["request", "projection"]


@dataclass
class AccountingRoleAppState:
    runtime: AccountingRequestRuntime | AccountingProjectionRuntime | None = None


class AccountingRoleAppOwner:
    def __init__(
        self,
        *,
        config: AccountingProtocolSettings,
        database: DatabaseConnectionSettings,
        lifecycle: ProcessLifecycle,
        role: AccountingWorkerRole,
        owner_id: str,
        signing_secret: str | None,
        verify_migrations: bool,
        pool_size: int,
        acquisition_seconds: float,
        lock_seconds: float,
        startup_seconds: float,
    ) -> None:
        if role not in {"request", "projection"} or not config.accounting_protocol_enabled:
            raise ValueError("accounting role app needs its enabled protocol and fixed role")
        if role == "request" and (signing_secret is None or not 1 <= len(signing_secret) <= 4096):
            raise ValueError("accounting request role needs a bounded signing secret")
        if type(startup_seconds) not in (int, float) or not 0 < startup_seconds <= 30:
            raise ValueError("accounting role startup budget is invalid")
        self.state = AccountingRoleAppState()
        self.lifecycle = lifecycle
        self.shutdown = ShutdownOwner(lifecycle)
        self._config = AccountingProtocolSettings.model_validate(config.model_dump())
        self._database = database
        self._role = role
        self._owner_id = owner_id
        self._secret = signing_secret
        self._verify = verify_migrations
        self._pool_size = pool_size
        self._acquisition = acquisition_seconds
        self._lock = lock_seconds
        self._startup = startup_seconds
        self._pool = AccountingPostgresManager()

    @asynccontextmanager
    async def scope(self, app: FastAPI):
        token = shutdown_owner.set(self.shutdown)
        try:
            async with BoundedExitStack(phase="close") as resources, BoundedExitStack() as workers:
                resources.push_async_callback(self._pool.disconnect)
                deadline = asyncio.get_running_loop().time() + self._startup
                async with asyncio.timeout_at(deadline):
                    await self._start(app, workers, deadline)
                    self.lifecycle.mark_serving()
                yield
        finally:
            self.lifecycle.mark_stopped()
            shutdown_owner.reset(token)

    async def _start(self, app: FastAPI, workers: BoundedExitStack, deadline: float) -> None:
        config = self._config
        await self._pool.connect(
            self._database,
            pool_size=self._pool_size,
            acquisition_seconds=self._acquisition,
            statement_seconds=config.accounting_statement_timeout_ms / 1000,
            lock_seconds=self._lock,
            allocation="dedicated",
        )
        client = self._pool.client
        if client is None:
            raise RuntimeError("accounting role database client is missing")
        if self._verify:
            await verify_migration_status(
                client, timeout_seconds=max(0, deadline - asyncio.get_running_loop().time())
            )
        runtime = (
            build_accounting_request_runtime(client, config, self.lifecycle)
            if self._role == "request"
            else build_accounting_projection_runtime(
                client,
                config,
                self.lifecycle,
                owner_id=self._owner_id,
            )
        )
        self.state.runtime = runtime
        workers.push_async_callback(self._close_runtime)
        await runtime.start(expires_at=deadline)
        if isinstance(runtime, AccountingRequestRuntime):
            secret = self._secret
            if secret is None:
                raise RuntimeError("accounting request signing secret is missing")
            app.include_router(accounting_rpc_router(runtime.service, signing_secret=secret))

    async def _close_runtime(self) -> None:
        runtime = self.state.runtime
        if runtime is not None:
            await runtime.close(
                expires_at=cleanup_deadline(self.lifecycle.settings.lifecycle_worker_drain_seconds)
            )


def create_accounting_worker_app(
    *,
    config: AccountingProtocolSettings,
    database: DatabaseConnectionSettings,
    lifecycle: ProcessLifecycle,
    role: AccountingWorkerRole,
    owner_id: str,
    signing_secret: str | None = None,
    verify_migrations: bool = True,
    pool_size: int = 2,
    acquisition_seconds: float = 0.2,
    lock_seconds: float = 0.2,
    startup_seconds: float = 5,
) -> FastAPI:
    owner = AccountingRoleAppOwner(
        config=config,
        database=database,
        lifecycle=lifecycle,
        role=role,
        owner_id=owner_id,
        signing_secret=signing_secret,
        verify_migrations=verify_migrations,
        pool_size=pool_size,
        acquisition_seconds=acquisition_seconds,
        lock_seconds=lock_seconds,
        startup_seconds=startup_seconds,
    )
    app = FastAPI(lifespan=owner.scope, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.process_lifecycle = lifecycle
    app.state.shutdown_owner = owner.shutdown
    app.state.accounting_role_state = owner.state
    app.state.ingress_runtime = IngressRuntime(
        IngressLimits(
            enabled=True,
            max_active=1,
            max_body_bytes=1_048_576,
            max_buffered_bytes=1_048_576,
            control_max_buffered_bytes=16_777_216,
            body_timeout_seconds=1,
        )
    )
    app.add_middleware(IngressMiddleware)
    _health_routes(app, lifecycle, owner.state)
    return app


def _health_routes(
    app: FastAPI, lifecycle: ProcessLifecycle, state: AccountingRoleAppState
) -> None:
    @app.get("/health")
    @app.get("/health/readiness")
    async def readiness() -> JSONResponse:
        runtime = state.runtime
        health = None if runtime is None else runtime.worker_health
        ready = lifecycle.ready and health is not None and health.state is WorkerState.READY
        return JSONResponse(
            {
                "status": "ok" if ready else "degraded",
                "checks": {"process": lifecycle.ready, "accounting": ready},
            },
            status_code=200 if ready else 503,
        )

    @app.get("/health/liveliness")
    async def liveness() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(get_prometheus_registry()), media_type=CONTENT_TYPE_LATEST)
