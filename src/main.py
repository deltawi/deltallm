from __future__ import annotations

import asyncio
import logging
from asyncio import Task, create_task
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from src.batch import BatchCleanupConfig, BatchRepository, BatchRetentionCleanupWorker
from src.batch.service import BatchService
from src.batch.storage import LocalBatchArtifactStorage
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.cache import (
    CacheMiddleware,
    configure_cache_runtime,
)
from src.billing import AlertService, BudgetEnforcementService, SpendLedgerService, SpendTrackingService
from src.callbacks import CallbackManager
from src.config import get_settings, resolve_salt_key
from src.config_runtime import DynamicConfigManager, ModelHotReloadManager, SecretResolver, build_app_config, load_yaml_dict
from src.db.client import prisma_manager
from src.db.repositories import AuditRepository, KeyRepository, ModelDeploymentRepository
from src.db.prompt_registry import PromptRegistryRepository
from src.db.route_groups import RouteGroupRepository
from src.auth import CustomAuthManager, InMemoryUserRepository, JWTAuthHandler, SSOAuthHandler, SSOConfig, SSOProvider
from src.api.admin import admin_router
from src.api.v1.router import v1_router
from src.guardrails.middleware import GuardrailMiddleware
from src.guardrails.registry import GuardrailRegistry
from src.middleware.errors import register_exception_handlers
from src.middleware.platform_auth import attach_platform_auth_context
from src.providers.healthcheck import probe_provider_health
from src.providers.anthropic import AnthropicAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter
from src.router import (
    BackgroundHealthChecker,
    CooldownManager,
    FallbackConfig,
    FailoverManager,
    HealthCheckConfig,
    HealthEndpointHandler,
    PassiveHealthTracker,
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.services.key_service import KeyService
from src.services.audit_retention import AuditRetentionConfig, AuditRetentionWorker
from src.services.audit_service import AuditService
from src.services.limit_counter import LimitCounter
from src.services.model_deployments import bootstrap_model_deployments_from_config, load_model_registry
from src.services.prompt_registry import PromptRegistryService
from src.services.route_groups import RouteGroupRuntimeCache, load_route_groups
from src.services.platform_identity_service import PlatformIdentityService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _normalize_fallbacks(items: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for item in items:
        for key, value in item.items():
            merged[key] = list(value)
    return merged


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    file_config = load_yaml_dict(settings.config_path)
    cfg = build_app_config(file_config, secret_resolver=SecretResolver())
    app.state.settings = settings
    app.state.app_config = cfg

    redis_client: Redis | None = None
    redis_url = settings.redis_url or cfg.general_settings.redis_url
    if redis_url:
        redis_client = Redis.from_url(redis_url, decode_responses=True)
    else:
        host = cfg.general_settings.redis_host or settings.redis_host
        port = cfg.general_settings.redis_port or settings.redis_port
        password = cfg.general_settings.redis_password or settings.redis_password
        redis_client = Redis(host=host, port=port, password=password, decode_responses=True)

    app.state.redis = redis_client
    app.state.route_group_runtime_cache = RouteGroupRuntimeCache(redis_client=redis_client)

    await prisma_manager.connect()
    app.state.prisma_manager = prisma_manager
    dynamic_config_manager = DynamicConfigManager(
        db_client=prisma_manager.client,
        redis_client=redis_client,
        file_config=file_config,
    )
    await dynamic_config_manager.initialize()
    cfg = dynamic_config_manager.get_app_config()
    app.state.dynamic_config_manager = dynamic_config_manager
    app.state.app_config = cfg
    salt_key = resolve_salt_key(cfg, settings)

    app.state.http_client = httpx.AsyncClient(timeout=60)
    app.state.openai_adapter = OpenAIAdapter(app.state.http_client)
    app.state.azure_openai_adapter = AzureOpenAIAdapter(app.state.http_client)
    app.state.anthropic_adapter = AnthropicAdapter(app.state.http_client)
    app.state.gemini_adapter = GeminiAdapter(app.state.http_client)
    app.state.bedrock_adapter = BedrockAdapter(app.state.http_client)
    app.state.key_service = KeyService(
        repository=KeyRepository(prisma_manager.client),
        redis_client=redis_client,
        salt=salt_key,
        auth_cache_ttl_seconds=cfg.general_settings.api_key_auth_cache_ttl_seconds,
    )
    app.state.audit_repository = None
    app.state.audit_service = None
    audit_retention_worker: AuditRetentionWorker | None = None
    audit_retention_task: Task[None] | None = None
    if cfg.general_settings.audit_enabled:
        app.state.audit_repository = AuditRepository(prisma_manager.client)
        app.state.audit_service = AuditService(app.state.audit_repository)
        await app.state.audit_service.start()
        if cfg.general_settings.audit_retention_worker_enabled:
            audit_retention_worker = AuditRetentionWorker(
                repository=app.state.audit_repository,
                config=AuditRetentionConfig(
                    interval_seconds=cfg.general_settings.audit_retention_interval_seconds,
                    scan_limit=cfg.general_settings.audit_retention_scan_limit,
                    metadata_retention_days=cfg.general_settings.audit_metadata_retention_days,
                    payload_retention_days=cfg.general_settings.audit_payload_retention_days,
                ),
            )
            audit_retention_task = create_task(audit_retention_worker.run())
    app.state.platform_identity_service = PlatformIdentityService(
        db_client=prisma_manager.client,
        salt=salt_key,
        session_ttl_hours=cfg.general_settings.auth_session_ttl_hours,
    )
    await app.state.platform_identity_service.ensure_bootstrap_admin(
        email=cfg.general_settings.platform_bootstrap_admin_email,
        password=cfg.general_settings.platform_bootstrap_admin_password,
    )
    app.state.limit_counter = LimitCounter(
        redis_client=redis_client,
        degraded_mode=str(cfg.general_settings.redis_degraded_mode or settings.redis_degraded_mode),
    )
    app.state.model_deployment_repository = ModelDeploymentRepository(prisma_manager.client)
    app.state.route_group_repository = RouteGroupRepository(prisma_manager.client)
    app.state.prompt_registry_repository = PromptRegistryRepository(prisma_manager.client)
    app.state.prompt_registry_service = PromptRegistryService(
        repository=app.state.prompt_registry_repository,
        route_group_repository=app.state.route_group_repository,
        redis_client=redis_client,
    )
    app.state.batch_repository = BatchRepository(prisma_manager.client)
    if cfg.general_settings.model_deployment_bootstrap_from_config:
        did_bootstrap = await bootstrap_model_deployments_from_config(app.state.model_deployment_repository, cfg)
        if did_bootstrap:
            logger.info("bootstrapped model deployments from config into database")
    app.state.model_registry, model_registry_source = await load_model_registry(
        app.state.model_deployment_repository,
        cfg,
        settings,
        source_mode=cfg.general_settings.model_deployment_source,
    )
    logger.info("loaded model registry from %s source", model_registry_source)
    app.state.route_groups, route_group_source = await load_route_groups(
        app.state.route_group_repository,
        cfg,
        route_group_cache=app.state.route_group_runtime_cache,
    )
    logger.info("loaded route groups from %s source", route_group_source)
    guardrail_registry = GuardrailRegistry()
    if cfg.deltallm_settings.guardrails:
        guardrail_registry.load_from_config(cfg.deltallm_settings.guardrails)
    app.state.guardrail_registry = guardrail_registry
    app.state.guardrail_middleware = GuardrailMiddleware(
        registry=guardrail_registry,
        cache_backend=redis_client,
    )
    callback_manager = CallbackManager()
    callback_manager.load_from_settings(
        success_callbacks=cfg.deltallm_settings.success_callback,
        failure_callbacks=cfg.deltallm_settings.failure_callback,
        callbacks=cfg.deltallm_settings.callbacks,
        callback_settings=cfg.deltallm_settings.callback_settings,
    )
    app.state.callback_manager = callback_manager
    app.state.turn_off_message_logging = cfg.deltallm_settings.turn_off_message_logging
    app.state.alert_service = AlertService(redis_client=redis_client)
    app.state.spend_ledger_service = SpendLedgerService(prisma_manager.client)
    app.state.spend_tracking_service = SpendTrackingService(
        db_client=prisma_manager.client,
        ledger=app.state.spend_ledger_service,
    )
    app.state.budget_service = BudgetEnforcementService(
        db_client=prisma_manager.client,
        alert_service=app.state.alert_service,
    )
    app.state.sso_user_repository = InMemoryUserRepository()
    app.state.sso_auth_handler = None
    if cfg.general_settings.enable_sso:
        required = (
            cfg.general_settings.sso_client_id,
            cfg.general_settings.sso_client_secret,
            cfg.general_settings.sso_authorize_url,
            cfg.general_settings.sso_token_url,
            cfg.general_settings.sso_userinfo_url,
            cfg.general_settings.sso_redirect_uri,
        )
        if all(required):
            app.state.sso_auth_handler = SSOAuthHandler(
                config=SSOConfig(
                    provider=SSOProvider(cfg.general_settings.sso_provider),
                    client_id=cfg.general_settings.sso_client_id or "",
                    client_secret=cfg.general_settings.sso_client_secret or "",
                    authorize_url=cfg.general_settings.sso_authorize_url or "",
                    token_url=cfg.general_settings.sso_token_url or "",
                    userinfo_url=cfg.general_settings.sso_userinfo_url or "",
                    redirect_uri=cfg.general_settings.sso_redirect_uri or "",
                    scope=cfg.general_settings.sso_scope,
                    admin_email_list=cfg.general_settings.sso_admin_email_list,
                    default_team_id=cfg.general_settings.sso_default_team_id,
                ),
                user_repository=app.state.sso_user_repository,
                http_client=app.state.http_client,
                rate_limiter=app.state.limit_counter,
            )
        else:
            logger.warning("sso enabled but configuration is incomplete")

    app.state.jwt_auth_handler = None
    if cfg.general_settings.enable_jwt_auth and cfg.general_settings.jwt_public_key_url:
        if not cfg.general_settings.jwt_issuer:
            raise ValueError("JWT issuer must be configured when JWT auth is enabled")
        app.state.jwt_auth_handler = JWTAuthHandler(
            jwks_url=cfg.general_settings.jwt_public_key_url,
            audience=cfg.general_settings.jwt_audience,
            issuer=cfg.general_settings.jwt_issuer,
            claims_mapping=cfg.general_settings.jwt_claims_mapping or None,
            http_client=app.state.http_client,
        )

    app.state.custom_auth_manager = None
    if cfg.general_settings.custom_auth:
        manager = CustomAuthManager()
        manager.register(cfg.general_settings.custom_auth)
        app.state.custom_auth_manager = manager

    state_backend = RedisStateBackend(redis_client)
    routing_strategy = RoutingStrategy(cfg.router_settings.routing_strategy)
    route_groups = list(getattr(app.state, "route_groups", []))
    deployment_registry = build_deployment_registry(app.state.model_registry, route_groups=route_groups)
    router_config = RouterConfig(
        num_retries=cfg.router_settings.num_retries,
        retry_after=cfg.router_settings.retry_after,
        timeout=cfg.router_settings.timeout,
        cooldown_time=cfg.router_settings.cooldown_time,
        allowed_fails=cfg.router_settings.allowed_fails,
        enable_pre_call_checks=cfg.router_settings.enable_pre_call_checks,
        model_group_alias=cfg.router_settings.model_group_alias,
        route_group_policies=build_route_group_policies(route_groups),
    )
    app.state.router = Router(
        strategy=routing_strategy,
        state_backend=state_backend,
        config=router_config,
        deployment_registry=deployment_registry,
    )
    app.state.router_state_backend = state_backend
    app.state.cooldown_manager = CooldownManager(
        state_backend=state_backend,
        cooldown_time=cfg.router_settings.cooldown_time,
        allowed_fails=cfg.router_settings.allowed_fails,
    )
    app.state.failover_manager = FailoverManager(
        config=FallbackConfig(
            num_retries=cfg.router_settings.num_retries,
            retry_after=cfg.router_settings.retry_after,
            timeout=cfg.router_settings.timeout,
            fallbacks=_normalize_fallbacks(cfg.deltallm_settings.fallbacks),
            context_window_fallbacks=_normalize_fallbacks(cfg.deltallm_settings.context_window_fallbacks),
            content_policy_fallbacks=_normalize_fallbacks(cfg.deltallm_settings.content_policy_fallbacks),
        ),
        deployment_registry=deployment_registry,
        state_backend=state_backend,
        cooldown_manager=app.state.cooldown_manager,
    )
    app.state.passive_health_tracker = PassiveHealthTracker(state_backend=state_backend)
    app.state.router_health_handler = HealthEndpointHandler(
        deployment_registry=deployment_registry,
        state_backend=state_backend,
    )
    configure_cache_runtime(
        app,
        app_config=cfg,
        redis_client=redis_client,
        salt_key=salt_key,
    )

    async def _deployment_health_check(deployment):
        return await probe_provider_health(
            app.state.http_client,
            deployment.deltallm_params,
            default_openai_base_url=app.state.settings.openai_base_url,
        )

    health_checker = BackgroundHealthChecker(
        config=HealthCheckConfig(
            enabled=cfg.general_settings.background_health_checks,
            interval_seconds=cfg.general_settings.health_check_interval,
            timeout_seconds=30,
        ),
        deployment_registry=deployment_registry,
        state_backend=state_backend,
        checker=_deployment_health_check,
    )
    app.state.background_health_checker = health_checker
    app.state.model_hot_reload_manager = ModelHotReloadManager(
        app=app,
        dynamic_config=dynamic_config_manager,
        model_repository=app.state.model_deployment_repository,
        route_group_repository=app.state.route_group_repository,
        route_group_cache=app.state.route_group_runtime_cache,
    )
    batch_worker: BatchExecutorWorker | None = None
    batch_worker_task: Task[None] | None = None
    batch_gc_worker: BatchRetentionCleanupWorker | None = None
    batch_gc_task: Task[None] | None = None
    if cfg.general_settings.embeddings_batch_enabled:
        batch_storage = LocalBatchArtifactStorage(cfg.general_settings.embeddings_batch_storage_dir)
        app.state.batch_storage = batch_storage
        app.state.batch_service = BatchService(
            repository=app.state.batch_repository,
            storage=batch_storage,
            metadata_retention_days=cfg.general_settings.batch_metadata_retention_days,
        )
        if cfg.general_settings.embeddings_batch_worker_enabled:
            batch_worker = BatchExecutorWorker(
                app=app,
                repository=app.state.batch_repository,
                storage=batch_storage,
                config=BatchWorkerConfig(
                    worker_id=f"worker-{id(app)}",
                    poll_interval_seconds=cfg.general_settings.embeddings_batch_poll_interval_seconds,
                    item_claim_limit=cfg.general_settings.embeddings_batch_item_claim_limit,
                    max_attempts=cfg.general_settings.embeddings_batch_max_attempts,
                    completed_artifact_retention_days=cfg.general_settings.batch_completed_artifact_retention_days,
                    failed_artifact_retention_days=cfg.general_settings.batch_failed_artifact_retention_days,
                ),
            )
            batch_worker_task = create_task(batch_worker.run())
        if cfg.general_settings.embeddings_batch_gc_enabled:
            batch_gc_worker = BatchRetentionCleanupWorker(
                repository=app.state.batch_repository,
                storage=batch_storage,
                config=BatchCleanupConfig(
                    interval_seconds=cfg.general_settings.embeddings_batch_gc_interval_seconds,
                    scan_limit=cfg.general_settings.embeddings_batch_gc_scan_limit,
                ),
            )
            batch_gc_task = create_task(batch_gc_worker.run())
    else:
        app.state.batch_storage = None
        app.state.batch_service = None
    health_task: Task[None] | None = None
    if cfg.general_settings.background_health_checks:
        health_task = create_task(health_checker.start())

    logger.info("application startup complete")
    try:
        yield
    finally:
        callback_manager: CallbackManager = getattr(app.state, "callback_manager", CallbackManager())
        await callback_manager.shutdown()
        if audit_retention_worker is not None:
            audit_retention_worker.stop()
        if audit_retention_task is not None:
            audit_retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await audit_retention_task
        audit_service: AuditService | None = getattr(app.state, "audit_service", None)
        if audit_service is not None:
            await audit_service.shutdown()
        await dynamic_config_manager.close()
        health_checker.stop()
        if health_task is not None:
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task
        if batch_worker is not None:
            batch_worker.stop()
        if batch_worker_task is not None:
            batch_worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await batch_worker_task
        if batch_gc_worker is not None:
            batch_gc_worker.stop()
        if batch_gc_task is not None:
            batch_gc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await batch_gc_task
        await app.state.http_client.aclose()
        if redis_client is not None:
            await redis_client.close()
        await prisma_manager.disconnect()


def create_app() -> FastAPI:
    app = FastAPI(title="DeltaLLM Core API", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.add_middleware(CacheMiddleware)

    @app.middleware("http")
    async def _platform_auth_context_middleware(request: Request, call_next):
        await attach_platform_auth_context(request)
        return await call_next(request)

    app.include_router(v1_router)
    app.include_router(admin_router)

    ui_dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
    if ui_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=str(ui_dist / "assets")), name="ui-assets")

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            del request
            if full_path.startswith(("ui/api/", "v1/", "auth/", "health/")):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            file_path = ui_dist / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(ui_dist / "index.html"))

    return app


app = create_app()
