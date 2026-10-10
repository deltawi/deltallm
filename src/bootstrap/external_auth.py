from __future__ import annotations

from fastapi import FastAPI
from prisma import Prisma

from src.auth.external_config import ExternalAuthSettings
from src.startup_settings import startup_setting
from src.auth.external_assertions import ExternalAssertionVerifier
from src.auth.external_crypto import ExternalCryptoExecutor
from src.bootstrap.status import BootstrapStatus
from src.config import AppConfig, resolve_external_auth_database_settings
from src.services.external_auth_admission import ExternalAuthAdmission
from src.services.external_auth_audit import ExternalAuthAudit
from src.services.external_auth_runtime import ExternalAuthRuntime
from src.services.limit_counter import LimitCounter


async def init_external_auth_runtime(app: FastAPI, cfg: AppConfig) -> ExternalAuthRuntime | None:
    app.state.external_auth_runtime = None
    settings = ExternalAuthSettings.model_validate(
        startup_setting(
            cfg.general_settings, app.state.settings, "external_auth", ExternalAuthSettings()
        )
    )
    app.state.external_auth_settings = settings
    if not settings.enabled:
        return None
    settings.require_deployment(
        audit_mode=cfg.general_settings.audit_ingestion_mode,
        audit_worker_enabled=cfg.general_settings.audit_ingestion_worker_enabled,
        cache_worker_enabled=cfg.general_settings.cache_invalidation_worker_enabled,
        cache_ttl_seconds=cfg.general_settings.api_key_auth_cache_ttl_seconds,
    )
    database = resolve_external_auth_database_settings(cfg, app.state.settings)
    audit = getattr(app.state, "audit_service", None)
    redis = getattr(app.state, "redis", None)
    if database is None or redis is None or audit is None or not audit.worker_health.ready:
        raise ValueError(
            "External auth requires PostgreSQL, Redis, and a healthy durable audit worker"
        )
    external_audit = ExternalAuthAudit(audit)
    if not cfg.general_settings.cache_invalidation_worker_enabled:
        raise ValueError("External auth requires the cache invalidation worker")
    if cfg.general_settings.api_key_auth_cache_ttl_seconds > 60:
        raise ValueError("External auth requires an API key auth cache lifetime at most 60 seconds")
    await redis.ping()
    crypto = ExternalCryptoExecutor(ExternalAssertionVerifier(settings))
    db = Prisma(datasource={"url": database.url})
    runtime = ExternalAuthRuntime(
        db=db,
        settings=settings,
        crypto=crypto,
        admission=ExternalAuthAdmission(
            LimitCounter(redis_client=redis, degraded_mode="fail_closed"),
            settings,
            environment=str(app.state.settings.app_env),
        ),
        audit=external_audit,
        identities=app.state.platform_identity_service,
    )
    try:
        await db.connect()
        await runtime.start()
    except BaseException:
        await runtime.close()
        raise
    app.state.external_auth_runtime = runtime
    return runtime


def external_auth_status(runtime: ExternalAuthRuntime | None) -> BootstrapStatus:
    return BootstrapStatus(
        "external_auth", "ready" if runtime is not None and runtime.ready else "disabled"
    )
