from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.auth import CustomAuthManager, InMemoryUserRepository, JWTAuthHandler, SSOAuthHandler, SSOConfig, SSOProvider
from src.db.repositories import KeyRepository
from src.services.key_service import KeyService
from src.services.limit_counter import LimitCounter
from src.services.platform_identity_service import PlatformIdentityService

logger = logging.getLogger(__name__)


@dataclass
class AuthRuntime:
    initialized: bool = True
    statuses: tuple[BootstrapStatus, ...] = ()


async def init_auth_runtime(app: Any, cfg: Any) -> AuthRuntime:
    statuses = [
        BootstrapStatus("key_service", "ready"),
        BootstrapStatus("platform_identity", "ready"),
    ]

    app.state.key_service = KeyService(
        repository=KeyRepository(app.state.prisma_manager.client),
        redis_client=app.state.redis,
        salt=app.state.salt_key,
        auth_cache_ttl_seconds=cfg.general_settings.api_key_auth_cache_ttl_seconds,
    )
    app.state.platform_identity_service = PlatformIdentityService(
        db_client=app.state.prisma_manager.client,
        salt=app.state.salt_key,
        session_ttl_hours=cfg.general_settings.auth_session_ttl_hours,
    )
    await app.state.platform_identity_service.ensure_bootstrap_admin(
        email=cfg.general_settings.platform_bootstrap_admin_email,
        password=cfg.general_settings.platform_bootstrap_admin_password,
    )
    app.state.limit_counter = LimitCounter(
        redis_client=app.state.redis,
        degraded_mode=str(cfg.general_settings.redis_degraded_mode or app.state.settings.redis_degraded_mode),
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
            statuses.append(BootstrapStatus("sso_auth", "ready"))
        else:
            logger.warning("sso enabled but configuration is incomplete")
            statuses.append(BootstrapStatus("sso_auth", "degraded", "configuration incomplete"))
    else:
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
            http_client=app.state.http_client,
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

    return AuthRuntime(statuses=tuple(statuses))
