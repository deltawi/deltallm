from __future__ import annotations

from asyncio import CancelledError, Task, create_task
from dataclasses import dataclass
import logging
import os
import socket
from typing import Any
from uuid import uuid4

from src.bootstrap.status import BootstrapStatus
from src.db.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.db.email_tokens import EmailTokenRepository
from src.db.invitations import InvitationRepository
from src.auth import (
    CustomAuthManager,
    InMemoryUserRepository,
    JWTAuthHandler,
    SSOAuthHandler,
    SSOConfig,
    SSOProvider,
)
from src.db.repositories import KeyRepository
from src.services.cache_invalidation import (
    CacheInvalidationService,
    CacheInvalidationWorker,
    CacheInvalidationWorkerConfig,
)
from src.services.email_token_service import EmailTokenService
from src.services.invitation_service import InvitationService
from src.services.key_service import KeyService
from src.services.limit_counter import LimitCounter
from src.services.master_session_service import MasterSessionService
from src.bootstrap.organization_deletion import (
    initialize_organization_deletion_runtime,
    initialize_organization_lifecycle,
    require_organization_deletion_readiness,
    start_organization_deletion_tasks,
)
from src.services.platform_identity_service import PlatformIdentityService
from src.services.self_registration_provisioning import SelfRegistrationProvisioningService
from src.services.sso_state_store import SSOStateStore

logger = logging.getLogger(__name__)
_AUTH_BOOT_ID = uuid4().hex[:12]


@dataclass
class AuthRuntime:
    initialized: bool = True
    organization_lifecycle_task: Task[None] | None = None
    cache_invalidation_worker: CacheInvalidationWorker | None = None
    cache_invalidation_task: Task[None] | None = None
    organization_deletion_worker: Any | None = None
    organization_deletion_task: Task[None] | None = None
    statuses: tuple[BootstrapStatus, ...] = ()


def _safe_worker_id_part(value: object, *, fallback: str) -> str:
    safe = "".join(
        char if char.isascii() and (char.isalnum() or char in {"-", "_", "."}) else "-"
        for char in str(value or "").strip()
    ).strip("-._")
    return safe or fallback


def _cache_invalidation_worker_id() -> str:
    return "-".join(
        (
            "cache-invalidation",
            _safe_worker_id_part(socket.gethostname(), fallback="unknown-host"),
            str(os.getpid()),
            _AUTH_BOOT_ID,
        )
    )


def _cache_invalidation_worker_config(general_settings: Any) -> CacheInvalidationWorkerConfig:
    lease_seconds = int(
        getattr(general_settings, "cache_invalidation_worker_lease_seconds", 60) or 60
    )
    configured_record_timeout_seconds = float(
        getattr(general_settings, "cache_invalidation_worker_record_timeout_seconds", 10.0) or 10.0
    )
    record_timeout_seconds = min(
        max(0.001, configured_record_timeout_seconds),
        max(0.001, float(lease_seconds) - 0.5),
    )
    return CacheInvalidationWorkerConfig(
        poll_interval_seconds=float(
            getattr(
                general_settings,
                "cache_invalidation_worker_poll_interval_seconds",
                5.0,
            )
            or 5.0
        ),
        max_batch_size=int(
            getattr(general_settings, "cache_invalidation_worker_batch_size", 25) or 25
        ),
        max_concurrency=int(
            getattr(
                general_settings,
                "cache_invalidation_worker_max_concurrency",
                4,
            )
            or 4
        ),
        lease_seconds=lease_seconds,
        record_timeout_seconds=record_timeout_seconds,
        max_attempts=int(getattr(general_settings, "cache_invalidation_max_attempts", 10) or 10),
        retry_initial_seconds=int(
            getattr(
                general_settings,
                "cache_invalidation_retry_initial_seconds",
                5,
            )
            or 5
        ),
        retry_max_seconds=int(
            getattr(general_settings, "cache_invalidation_retry_max_seconds", 300) or 300
        ),
    )


async def init_auth_runtime(app: Any, cfg: Any) -> AuthRuntime:
    statuses = [
        BootstrapStatus("key_service", "ready"),
        BootstrapStatus("platform_identity", "ready"),
        BootstrapStatus("master_session_store", "ready"),
    ]
    runtime = AuthRuntime()

    organization_deletion_repository = initialize_organization_lifecycle(app, cfg)
    await app.state.organization_lifecycle_authorizer.initialize()
    await require_organization_deletion_readiness(
        app.state.prisma_manager.client,
        requests_enabled=bool(
            getattr(
                cfg.general_settings,
                "organization_deletion_requests_enabled",
                False,
            )
        ),
    )
    app.state.key_service = KeyService(
        repository=KeyRepository(app.state.prisma_manager.client),
        redis_client=app.state.redis,
        salt=app.state.salt_key,
        auth_cache_ttl_seconds=cfg.general_settings.api_key_auth_cache_ttl_seconds,
        lifecycle_authorizer=app.state.organization_lifecycle_authorizer,
    )
    cache_invalidation_repository = getattr(
        app.state,
        "cache_invalidation_outbox_repository",
        CacheInvalidationOutboxRepository(app.state.prisma_manager.client),
    )
    app.state.cache_invalidation_outbox_repository = cache_invalidation_repository
    app.state.cache_invalidation_service = CacheInvalidationService(
        key_service=app.state.key_service,
        repository=cache_invalidation_repository,
        max_attempts=int(
            getattr(cfg.general_settings, "cache_invalidation_max_attempts", 10) or 10
        ),
        immediate_timeout_seconds=float(
            getattr(cfg.general_settings, "cache_invalidation_immediate_timeout_seconds", 0.5)
            or 0.5
        ),
    )
    statuses.append(BootstrapStatus("cache_invalidation_outbox", "ready"))
    initialize_organization_deletion_runtime(
        app,
        cfg,
        runtime,
        organization_deletion_repository,
        statuses,
    )

    cache_invalidation_worker_enabled = bool(
        getattr(cfg.general_settings, "cache_invalidation_worker_enabled", True)
    )
    if cache_invalidation_worker_enabled and app.state.redis is None:
        app.state.cache_invalidation_worker = None
        statuses.append(
            BootstrapStatus("cache_invalidation_worker", "degraded", "redis unavailable")
        )
    elif cache_invalidation_worker_enabled:
        runtime.cache_invalidation_worker = CacheInvalidationWorker(
            repository=cache_invalidation_repository,
            key_service=app.state.key_service,
            worker_id=_cache_invalidation_worker_id(),
            config=_cache_invalidation_worker_config(cfg.general_settings),
        )
        app.state.cache_invalidation_worker = runtime.cache_invalidation_worker
        statuses.append(BootstrapStatus("cache_invalidation_worker", "ready"))
    else:
        app.state.cache_invalidation_worker = None
        statuses.append(BootstrapStatus("cache_invalidation_worker", "disabled"))

    app.state.platform_identity_service = PlatformIdentityService(
        db_client=app.state.prisma_manager.client,
        salt=app.state.salt_key,
        session_ttl_hours=cfg.general_settings.auth_session_ttl_hours,
    )
    app.state.platform_identity_service.totp_issuer = str(
        getattr(cfg.general_settings, "instance_name", "DeltaLLM") or "DeltaLLM"
    )
    await app.state.platform_identity_service.ensure_bootstrap_admin(
        email=cfg.general_settings.platform_bootstrap_admin_email,
        password=cfg.general_settings.platform_bootstrap_admin_password,
    )
    app.state.master_session_service = MasterSessionService(
        db_client=app.state.prisma_manager.client,
        salt=app.state.salt_key,
    )
    app.state.self_registration_provisioning_service = SelfRegistrationProvisioningService(
        db_client=app.state.prisma_manager.client,
        platform_identity_service=app.state.platform_identity_service,
    )
    app.state.limit_counter = LimitCounter(
        redis_client=app.state.redis,
        degraded_mode=str(
            cfg.general_settings.redis_degraded_mode or app.state.settings.redis_degraded_mode
        ),
    )
    app.state.email_token_service = EmailTokenService(
        repository=getattr(
            app.state,
            "email_token_repository",
            EmailTokenRepository(app.state.prisma_manager.client),
        ),
        salt=app.state.salt_key,
        config_getter=lambda: getattr(app.state, "app_config", cfg),
    )
    app.state.invitation_service = InvitationService(
        db_client=app.state.prisma_manager.client,
        repository=getattr(
            app.state,
            "invitation_repository",
            InvitationRepository(app.state.prisma_manager.client),
        ),
        token_service=app.state.email_token_service,
        outbox_service=getattr(app.state, "email_outbox_service", None),
        platform_identity_service=app.state.platform_identity_service,
        config_getter=lambda: getattr(app.state, "app_config", cfg),
    )

    app.state.sso_user_repository = InMemoryUserRepository()
    app.state.sso_auth_handler = None
    app.state.sso_state_store = None
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
            if app.state.redis is None:
                statuses.append(BootstrapStatus("sso_state_store", "degraded", "redis unavailable"))
                statuses.append(BootstrapStatus("sso_auth", "degraded", "redis unavailable"))
            else:
                app.state.sso_state_store = SSOStateStore(
                    redis_client=app.state.redis,
                    ttl_seconds=getattr(cfg.general_settings, "sso_state_ttl_seconds", 600),
                )
                statuses.append(BootstrapStatus("sso_state_store", "ready"))
                control_http_client = getattr(
                    app.state, "control_http_client", app.state.http_client
                )
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
                    http_client=control_http_client,
                    rate_limiter=app.state.limit_counter,
                )
                statuses.append(BootstrapStatus("sso_auth", "ready"))
        else:
            logger.warning("sso enabled but configuration is incomplete")
            statuses.append(
                BootstrapStatus("sso_state_store", "degraded", "configuration incomplete")
            )
            statuses.append(BootstrapStatus("sso_auth", "degraded", "configuration incomplete"))
    else:
        statuses.append(BootstrapStatus("sso_state_store", "disabled"))
        statuses.append(BootstrapStatus("sso_auth", "disabled"))

    app.state.jwt_auth_handler = None
    if cfg.general_settings.enable_jwt_auth and cfg.general_settings.jwt_public_key_url:
        if not cfg.general_settings.jwt_issuer:
            raise ValueError("JWT issuer must be configured when JWT auth is enabled")
        app.state.jwt_auth_handler = JWTAuthHandler(
            jwks_url=cfg.general_settings.jwt_public_key_url,
            audience=cfg.general_settings.jwt_audience,
            issuer=cfg.general_settings.jwt_issuer,
            claims_mapping=cfg.general_settings.jwt_claims_mapping or None,
            http_client=getattr(app.state, "control_http_client", app.state.http_client),
        )
        statuses.append(BootstrapStatus("jwt_auth", "ready"))
    else:
        statuses.append(BootstrapStatus("jwt_auth", "disabled"))

    app.state.custom_auth_manager = None
    if cfg.general_settings.custom_auth:
        manager = CustomAuthManager()
        manager.register(cfg.general_settings.custom_auth)
        app.state.custom_auth_manager = manager
        statuses.append(BootstrapStatus("custom_auth", "ready"))
    else:
        statuses.append(BootstrapStatus("custom_auth", "disabled"))

    start_organization_deletion_tasks(app, runtime)
    if runtime.cache_invalidation_worker is not None:
        runtime.cache_invalidation_task = create_task(runtime.cache_invalidation_worker.run())

    runtime.statuses = tuple(statuses)
    return runtime


async def shutdown_auth_runtime(runtime: AuthRuntime) -> None:
    lifecycle_task = getattr(runtime, "organization_lifecycle_task", None)
    if lifecycle_task is not None:
        lifecycle_task.cancel()
        try:
            await lifecycle_task
        except CancelledError:
            pass
    worker = getattr(runtime, "cache_invalidation_worker", None)
    task = getattr(runtime, "cache_invalidation_task", None)
    if worker is not None:
        worker.stop()
    if task is not None:
        task.cancel()
        try:
            await task
        except CancelledError:
            pass
    deletion_worker = getattr(runtime, "organization_deletion_worker", None)
    deletion_task = getattr(runtime, "organization_deletion_task", None)
    if deletion_worker is not None:
        deletion_worker.stop()
    if deletion_task is not None:
        deletion_task.cancel()
        try:
            await deletion_task
        except CancelledError:
            pass
