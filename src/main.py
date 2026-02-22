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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from src.cache import (
    CacheKeyBuilder,
    CacheMiddleware,
    InMemoryBackend,
    NoopCacheMetrics,
    PrometheusCacheMetrics,
    RedisBackend,
    S3Backend,
    StreamingCacheHandler,
)
from src.billing import AlertService, BudgetEnforcementService, SpendLedgerService, SpendTrackingService
from src.callbacks import CallbackManager
from src.config import AppConfig, get_settings
from src.config_runtime import DynamicConfigManager, ModelHotReloadManager, SecretResolver, build_app_config, load_yaml_dict
from src.db.client import prisma_manager
from src.db.repositories import KeyRepository
from src.auth import CustomAuthManager, InMemoryUserRepository, JWTAuthHandler, SSOAuthHandler, SSOConfig, SSOProvider
from src.api.admin import admin_router
from src.api.v1.router import v1_router
from src.guardrails.middleware import GuardrailMiddleware
from src.guardrails.registry import GuardrailRegistry
from src.middleware.errors import register_exception_handlers
from src.middleware.platform_auth import attach_platform_auth_context
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
)
from src.services.key_service import KeyService
from src.services.limit_counter import LimitCounter
from src.services.platform_identity_service import PlatformIdentityService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _build_model_registry(cfg: AppConfig, settings: Any) -> dict[str, list[dict[str, Any]]]:
    model_registry: dict[str, list[dict[str, Any]]] = {}
    for entry in cfg.model_list:
        params = entry.litellm_params.model_dump(exclude_none=True)
        if not params.get("api_key") and settings.openai_api_key:
            params["api_key"] = settings.openai_api_key
        if not params.get("api_base"):
            params["api_base"] = settings.openai_base_url
        model_info = entry.model_info.model_dump(exclude_none=True) if entry.model_info else {}
        model_registry.setdefault(entry.model_name, []).append(
            {
                "litellm_params": params,
                "model_info": model_info,
            }
        )
    return model_registry


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

    app.state.http_client = httpx.AsyncClient(timeout=60)
    app.state.openai_adapter = OpenAIAdapter(app.state.http_client)
    app.state.key_service = KeyService(
        repository=KeyRepository(prisma_manager.client),
        redis_client=redis_client,
        salt=cfg.general_settings.salt_key or settings.salt_key,
    )
    app.state.platform_identity_service = PlatformIdentityService(
        db_client=prisma_manager.client,
        salt=cfg.general_settings.salt_key or settings.salt_key,
        session_ttl_hours=cfg.general_settings.auth_session_ttl_hours,
    )
    await app.state.platform_identity_service.ensure_bootstrap_admin(
        email=cfg.general_settings.platform_bootstrap_admin_email,
        password=cfg.general_settings.platform_bootstrap_admin_password,
    )
    app.state.limit_counter = LimitCounter(redis_client=redis_client)
    app.state.model_registry = _build_model_registry(cfg, settings)
    guardrail_registry = GuardrailRegistry()
    if cfg.litellm_settings.guardrails:
        guardrail_registry.load_from_config(cfg.litellm_settings.guardrails)
    app.state.guardrail_registry = guardrail_registry
    app.state.guardrail_middleware = GuardrailMiddleware(
        registry=guardrail_registry,
        cache_backend=redis_client,
    )
    callback_manager = CallbackManager()
    callback_manager.load_from_settings(
        success_callbacks=cfg.litellm_settings.success_callback,
        failure_callbacks=cfg.litellm_settings.failure_callback,
        callbacks=cfg.litellm_settings.callbacks,
        callback_settings=cfg.litellm_settings.callback_settings,
    )
    app.state.callback_manager = callback_manager
    app.state.turn_off_message_logging = cfg.litellm_settings.turn_off_message_logging
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
            )
        else:
            logger.warning("sso enabled but configuration is incomplete")

    app.state.jwt_auth_handler = None
    if cfg.general_settings.enable_jwt_auth and cfg.general_settings.jwt_public_key_url:
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
    deployment_registry = build_deployment_registry(app.state.model_registry)
    router_config = RouterConfig(
        num_retries=cfg.router_settings.num_retries,
        retry_after=cfg.router_settings.retry_after,
        timeout=cfg.router_settings.timeout,
        cooldown_time=cfg.router_settings.cooldown_time,
        allowed_fails=cfg.router_settings.allowed_fails,
        enable_pre_call_checks=cfg.router_settings.enable_pre_call_checks,
        model_group_alias=cfg.router_settings.model_group_alias,
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
            fallbacks=_normalize_fallbacks(cfg.litellm_settings.fallbacks),
            context_window_fallbacks=_normalize_fallbacks(cfg.litellm_settings.context_window_fallbacks),
            content_policy_fallbacks=_normalize_fallbacks(cfg.litellm_settings.content_policy_fallbacks),
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
    cache_settings = cfg.general_settings
    app.state.cache_backend = None
    app.state.cache_key_builder = None
    app.state.cache_metrics = NoopCacheMetrics()
    app.state.streaming_cache_handler = None
    if cache_settings.cache_enabled:
        if cache_settings.cache_backend == "memory":
            cache_backend = InMemoryBackend(max_size=cache_settings.cache_max_size)
        elif cache_settings.cache_backend == "redis":
            cache_backend = RedisBackend(redis_client)
        elif cache_settings.cache_backend == "s3":
            cache_backend = S3Backend()
        else:
            raise ValueError(f"Unsupported cache backend: {cache_settings.cache_backend}")

        app.state.cache_backend = cache_backend
        app.state.cache_key_builder = CacheKeyBuilder(custom_salt=cfg.general_settings.salt_key or settings.salt_key)
        app.state.streaming_cache_handler = StreamingCacheHandler(cache_backend)
        try:
            app.state.cache_metrics = PrometheusCacheMetrics(cache_type=cache_settings.cache_backend)
        except Exception:
            app.state.cache_metrics = NoopCacheMetrics()

    async def _deployment_health_check(deployment) -> bool:
        return await app.state.openai_adapter.health_check(deployment.litellm_params)

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
    )
    health_task: Task[None] | None = None
    if cfg.general_settings.background_health_checks:
        health_task = create_task(health_checker.start())

    logger.info("application startup complete")
    try:
        yield
    finally:
        callback_manager: CallbackManager = getattr(app.state, "callback_manager", CallbackManager())
        await callback_manager.shutdown()
        await dynamic_config_manager.close()
        health_checker.stop()
        if health_task is not None:
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task
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
            file_path = ui_dist / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(ui_dist / "index.html"))

    return app


app = create_app()
