from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from redis.asyncio import Redis

from src.bootstrap.status import BootstrapStatus
from src.batch import BatchRepository
from src.config import (
    get_settings,
    resolve_database_settings,
    resolve_salt_key,
    resolve_telemetry_database_settings,
)
from src.config_runtime import (
    DynamicConfigManager,
    SecretResolver,
    build_app_config,
    load_yaml_dict,
)
from src.db.callable_target_access_groups import CallableTargetAccessGroupBindingRepository
from src.db.callable_targets import CallableTargetBindingRepository
from src.db.callable_target_policies import CallableTargetScopePolicyRepository
from src.db.client import prisma_manager, telemetry_prisma_manager
from src.db.email import EmailOutboxRepository
from src.db.email_tokens import EmailTokenRepository
from src.db.invitations import InvitationRepository
from src.db.mcp import MCPRepository
from src.db.mcp_scope_policies import MCPScopePolicyRepository
from src.db.named_credentials import NamedCredentialRepository
from src.db.prompt_registry import PromptRegistryRepository
from src.db.repositories import ModelDeploymentRepository
from src.db.route_groups import RouteGroupRepository
from src.db.tiers import TierRepository
from src.providers.anthropic import AnthropicAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter
from src.providers.registry import ProviderErrorMapperRegistry
from src.services.route_groups import RouteGroupRuntimeCache
from src.services.ui_branding_assets import UIBrandingAssetService
from src.services.route_group_mutations import RouteGroupMutationService
from src.upstream_http import build_control_http_client, build_upstream_http_client


@dataclass
class InfrastructureRuntime:
    redis_client: Redis | None
    dynamic_config_manager: DynamicConfigManager
    http_client: httpx.AsyncClient
    control_http_client: httpx.AsyncClient
    telemetry_database_connected: bool = False
    statuses: tuple[BootstrapStatus, ...] = ()


def _build_redis_client(settings: Any, cfg: Any) -> Redis:
    redis_url = settings.redis_url or cfg.general_settings.redis_url
    if redis_url:
        return Redis.from_url(redis_url, decode_responses=True)

    host = cfg.general_settings.redis_host or settings.redis_host
    port = cfg.general_settings.redis_port or settings.redis_port
    password = cfg.general_settings.redis_password or settings.redis_password
    return Redis(host=host, port=port, password=password, decode_responses=True)


def _startup_setting(general_settings: Any, settings: Any, field_name: str, default: Any) -> Any:
    fields_set = getattr(general_settings, "model_fields_set", None)
    if fields_set is None or field_name in fields_set:
        value = getattr(general_settings, field_name, None)
        if value is not None:
            return value
    return getattr(settings, field_name, default)


async def init_infrastructure_runtime(app: Any) -> InfrastructureRuntime:
    settings = get_settings()
    file_config = load_yaml_dict(settings.config_path)
    cfg = build_app_config(file_config, secret_resolver=SecretResolver())

    app.state.settings = settings
    app.state.app_config = cfg

    redis_client = _build_redis_client(settings, cfg)
    app.state.redis = redis_client
    app.state.route_group_runtime_cache = RouteGroupRuntimeCache(redis_client=redis_client)

    database_settings = resolve_database_settings(cfg, settings)
    await prisma_manager.connect(database_settings)
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
    app.state.salt_key = resolve_salt_key(cfg, settings)

    spend_ingestion_mode = str(
        _startup_setting(cfg.general_settings, settings, "spend_ingestion_mode", "legacy")
    )
    audit_ingestion_mode = str(
        _startup_setting(cfg.general_settings, settings, "audit_ingestion_mode", "legacy")
    )
    app.state.spend_ingestion_mode = spend_ingestion_mode
    app.state.audit_ingestion_mode = audit_ingestion_mode
    durable_telemetry_enabled = spend_ingestion_mode == "outbox" or audit_ingestion_mode == "outbox"
    telemetry_database_connected = False
    app.state.telemetry_prisma_manager = telemetry_prisma_manager
    if durable_telemetry_enabled:
        telemetry_database_settings = resolve_telemetry_database_settings(cfg, settings)
        if telemetry_database_settings is None:
            raise RuntimeError("durable telemetry ingestion requires an explicit database URL")
        await telemetry_prisma_manager.connect(telemetry_database_settings)
        if telemetry_prisma_manager.client is None:
            raise RuntimeError("durable telemetry ingestion requires the Prisma client")
        telemetry_database_connected = True

    ui_branding_asset_service = UIBrandingAssetService(prisma_manager.client)
    await ui_branding_asset_service.initialize(cfg)
    dynamic_config_manager.subscribe(ui_branding_asset_service.on_config_change)
    app.state.ui_branding_asset_service = ui_branding_asset_service

    http_client = build_upstream_http_client(cfg.general_settings)
    control_http_client = build_control_http_client()
    app.state.upstream_http_settings = cfg.general_settings
    app.state.http_client = http_client
    app.state.control_http_client = control_http_client
    app.state.openai_adapter = OpenAIAdapter(http_client)
    app.state.azure_openai_adapter = AzureOpenAIAdapter(http_client)
    app.state.anthropic_adapter = AnthropicAdapter(http_client)
    app.state.gemini_adapter = GeminiAdapter(http_client)
    app.state.bedrock_adapter = BedrockAdapter(http_client)
    app.state.provider_error_mapper_registry = ProviderErrorMapperRegistry(
        openai=app.state.openai_adapter,
        azure_openai=app.state.azure_openai_adapter,
        anthropic=app.state.anthropic_adapter,
        gemini=app.state.gemini_adapter,
        bedrock=app.state.bedrock_adapter,
    )

    app.state.model_deployment_repository = ModelDeploymentRepository(prisma_manager.client)
    app.state.named_credential_repository = NamedCredentialRepository(prisma_manager.client)
    app.state.callable_target_binding_repository = CallableTargetBindingRepository(
        prisma_manager.client
    )
    app.state.callable_target_access_group_repository = CallableTargetAccessGroupBindingRepository(
        prisma_manager.client
    )
    app.state.callable_target_scope_policy_repository = CallableTargetScopePolicyRepository(
        prisma_manager.client
    )
    app.state.route_group_repository = RouteGroupRepository(prisma_manager.client)
    app.state.route_group_mutation_service = RouteGroupMutationService(
        route_groups=app.state.route_group_repository,
        callable_bindings=app.state.callable_target_binding_repository,
        model_deployments=app.state.model_deployment_repository,
        model_registry_getter=lambda: getattr(app.state, "model_registry", None),
    )
    app.state.tier_repository = TierRepository(prisma_manager.client)
    app.state.prompt_registry_repository = PromptRegistryRepository(prisma_manager.client)
    app.state.mcp_repository = MCPRepository(prisma_manager.client)
    app.state.mcp_scope_policy_repository = MCPScopePolicyRepository(prisma_manager.client)
    app.state.batch_repository = BatchRepository(
        prisma_manager.client,
        webhook_max_attempts=getattr(cfg.general_settings, "batch_webhook_max_attempts", 8),
    )
    app.state.email_outbox_repository = EmailOutboxRepository(prisma_manager.client)
    app.state.email_token_repository = EmailTokenRepository(prisma_manager.client)
    app.state.invitation_repository = InvitationRepository(prisma_manager.client)

    return InfrastructureRuntime(
        redis_client=redis_client,
        dynamic_config_manager=dynamic_config_manager,
        http_client=http_client,
        control_http_client=control_http_client,
        telemetry_database_connected=telemetry_database_connected,
        statuses=(
            BootstrapStatus("config", "ready"),
            BootstrapStatus("redis", "ready"),
            BootstrapStatus("database", "ready"),
            BootstrapStatus("dynamic_config", "ready"),
            BootstrapStatus("ui_branding_assets", "ready"),
            BootstrapStatus("http_client", "ready"),
            BootstrapStatus("control_http_client", "ready"),
            BootstrapStatus("provider_adapters", "ready"),
        ),
    )


async def shutdown_infrastructure_runtime(runtime: InfrastructureRuntime) -> None:
    await runtime.dynamic_config_manager.close()
    await runtime.http_client.aclose()
    await runtime.control_http_client.aclose()
    if runtime.redis_client is not None:
        await runtime.redis_client.close()
    if runtime.telemetry_database_connected:
        await telemetry_prisma_manager.disconnect()
    await prisma_manager.disconnect()
