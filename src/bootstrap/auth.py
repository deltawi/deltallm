from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.db.email_tokens import EmailTokenRepository
from src.db.invitations import InvitationRepository
from src.auth import CustomAuthManager, InMemoryUserRepository, JWTAuthHandler, SSOAuthHandler, SSOConfig, SSOProvider
from src.db.repositories import KeyRepository
from src.services.email_token_service import EmailTokenService
from src.services.invitation_service import InvitationService
from src.services.key_service import KeyService
from src.services.limit_counter import LimitCounter
from src.services.platform_identity_service import PlatformIdentityService
from src.services.sso_state_store import SSOStateStore

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
    app.state.email_token_service = EmailTokenService(
        repository=getattr(app.state, "email_token_repository", EmailTokenRepository(app.state.prisma_manager.client)),
        salt=app.state.salt_key,
        config_getter=lambda: getattr(app.state, "app_config", cfg),
    )
    app.state.invitation_service = InvitationService(
        db_client=app.state.prisma_manager.client,
        repository=getattr(app.state, "invitation_repository", InvitationRepository(app.state.prisma_manager.client)),
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
            statuses.append(BootstrapStatus("sso_state_store", "degraded", "configuration incomplete"))
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
