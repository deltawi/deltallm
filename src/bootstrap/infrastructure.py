from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import httpx
from redis.asyncio import Redis

from src.bootstrap.status import BootstrapStatus
from src.bootstrap.dependency_capacity import DependencyAllocationSnapshot
from src.redis_runtime import build_redis_client
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
from src.spend_operation_settings import SpendOperationAllocation
from src.db.client import (
    prisma_manager,
    telemetry_prisma_manager,
    foreground_prisma_manager,
    telemetry_worker_prisma_manager,
    telemetry_settlement_prisma_manager,
)
from src.db.allocation_config import DatabasePolicy
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
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.profiled_chat import ProfiledChatAdapter
from src.providers.registry import ProviderErrorMapperRegistry
from src.router.redis_keys import RouteGroupRuntimeRedisKeyspace
from src.services.route_groups import RouteGroupRuntimeCache
from src.services.ui_branding_assets import UIBrandingAssetService
from src.services.route_group_mutations import RouteGroupMutationService
from src.upstream_http import (
    build_control_http_client,
    build_control_http_transport,
    build_upstream_http_client,
)
from src.outbound.network_policy import OutboundNetworkPolicy
from src.providers.discovery_runtime import ProviderDiscoveryRuntime


@dataclass
class InfrastructureRuntime:
    redis_client: Redis | None
    dynamic_config_manager: DynamicConfigManager
    http_client: httpx.AsyncClient
    control_http_client: httpx.AsyncClient
    telemetry_database_connected: bool = False
    statuses: tuple[BootstrapStatus, ...] = ()
    bulk_redis_client: Redis | None = None
    cache_redis_client: Redis | None = None
    cleanup: AsyncExitStack | None = None


def _startup_setting(general_settings: Any, settings: Any, field_name: str, default: Any) -> Any:
    fields_set = getattr(general_settings, "model_fields_set", None)
    if fields_set is None or field_name in fields_set:
        value = getattr(general_settings, field_name, None)
        if value is not None:
            return value
    return getattr(settings, field_name, default)


async def init_infrastructure_runtime(app: Any) -> InfrastructureRuntime:
    async with AsyncExitStack() as cleanup:
        resources = await cleanup.enter_async_context(AsyncExitStack())
        runtime = await _init_infrastructure_runtime(app, cleanup, resources)
        runtime.cleanup = cleanup.pop_all()
        return runtime


async def _init_infrastructure_runtime(
    app: Any, cleanup: AsyncExitStack, resources: AsyncExitStack
) -> InfrastructureRuntime:
    settings = get_settings()
    file_config = load_yaml_dict(settings.config_path)
    cfg = build_app_config(file_config, secret_resolver=SecretResolver())

    app.state.settings = settings
    app.state.app_config = cfg
    redis_endpoint_settings = cfg.general_settings

    database_settings = resolve_database_settings(cfg, settings)
    startup_allocations = DependencyAllocationSnapshot.build(cfg, settings)
    database_allocations = startup_allocations.database
    if database_settings is None:
        raise RuntimeError("Database allocations require an explicit database URL")
    resources.push_async_callback(prisma_manager.disconnect)
    await prisma_manager.connect(
        database_settings,
        policy=DatabasePolicy.build(
            database_allocations,
            "control",
            database_settings.pool_size,
        ),
    )
    app.state.prisma_manager = prisma_manager

    dynamic_config_manager = DynamicConfigManager(
        db_client=prisma_manager.client,
        redis_client=None,
        file_config=file_config,
    )
    cleanup.push_async_callback(dynamic_config_manager.close)
    await dynamic_config_manager.initialize()
    cfg = dynamic_config_manager.get_app_config()
    startup_allocations.validate_effective(cfg, settings)
    resources.push_async_callback(foreground_prisma_manager.disconnect)
    await foreground_prisma_manager.connect(
        database_settings,
        policy=DatabasePolicy.build(
            database_allocations,
            "foreground",
            database_allocations.db_foreground_pool_size,
        ),
    )
    app.state.foreground_prisma_manager = foreground_prisma_manager

    redis_client = build_redis_client(
        settings,
        cfg.general_settings,
        allocation="critical",
        endpoint_settings=redis_endpoint_settings,
    )
    resources.push_async_callback(redis_client.aclose)
    bulk_redis_client = build_redis_client(
        settings, cfg.general_settings, allocation="bulk", endpoint_settings=redis_endpoint_settings
    )
    resources.push_async_callback(bulk_redis_client.aclose)
    app.state.redis = redis_client
    app.state.bulk_redis = bulk_redis_client
    cache_redis_client = build_redis_client(
        settings,
        cfg.general_settings,
        allocation="cache",
        endpoint_settings=redis_endpoint_settings,
    )
    resources.push_async_callback(cache_redis_client.aclose)
    app.state.cache_redis = cache_redis_client
    dynamic_config_manager.attach_redis(redis_client)
    app.state.route_group_runtime_cache = RouteGroupRuntimeCache(
        redis_client=cache_redis_client,
        keyspace=RouteGroupRuntimeRedisKeyspace(environment=str(settings.app_env)),
    )

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
    app.state.telemetry_worker_prisma_manager = telemetry_worker_prisma_manager
    app.state.telemetry_settlement_prisma_manager = telemetry_settlement_prisma_manager
    app.state.spend_operation_intents_enabled = False
    if durable_telemetry_enabled:
        telemetry_database_settings = resolve_telemetry_database_settings(cfg, settings)
        if telemetry_database_settings is None:
            raise RuntimeError("durable telemetry ingestion requires an explicit database URL")
        operation_allocation = SpendOperationAllocation.resolve(
            cfg.general_settings,
            settings,
            telemetry_connections=telemetry_database_settings.pool_size,
        )
        app.state.spend_operation_intents_enabled = operation_allocation.enabled
        if operation_allocation.enabled:
            resources.push_async_callback(telemetry_settlement_prisma_manager.disconnect)
            await telemetry_settlement_prisma_manager.connect(
                telemetry_database_settings,
                policy=DatabasePolicy.build(
                    database_allocations,
                    "telemetry_settlement",
                    operation_allocation.settlement_connections,
                ),
            )
            if telemetry_settlement_prisma_manager.client is None:
                raise RuntimeError("Spend recovery requires its settlement allocation")
        resources.push_async_callback(telemetry_prisma_manager.disconnect)
        await telemetry_prisma_manager.connect(
            telemetry_database_settings,
            policy=DatabasePolicy.build(
                database_allocations,
                "telemetry",
                telemetry_database_settings.pool_size - operation_allocation.settlement_connections,
            ),
        )
        if telemetry_prisma_manager.client is None:
            raise RuntimeError("durable telemetry ingestion requires the Prisma client")
        telemetry_database_connected = True
        resources.push_async_callback(telemetry_worker_prisma_manager.disconnect)
        await telemetry_worker_prisma_manager.connect(
            telemetry_database_settings,
            policy=DatabasePolicy.build(
                database_allocations,
                "telemetry_worker",
                database_allocations.telemetry_worker_db_pool_size,
            ),
        )
        if telemetry_worker_prisma_manager.client is None:
            raise RuntimeError("Durable telemetry requires its worker database allocation")

    ui_branding_asset_service = UIBrandingAssetService(prisma_manager.client)
    await ui_branding_asset_service.initialize(cfg)
    dynamic_config_manager.subscribe(ui_branding_asset_service.on_config_change)
    app.state.ui_branding_asset_service = ui_branding_asset_service

    http_client = build_upstream_http_client(cfg.general_settings)
    resources.push_async_callback(http_client.aclose)
    control_transport = build_control_http_transport()
    control_http_client = build_control_http_client(transport=control_transport)
    resources.push_async_callback(control_http_client.aclose)
    app.state.provider_discovery_runtime = ProviderDiscoveryRuntime(
        transport=control_transport,
        policy=OutboundNetworkPolicy(
            allow_http=cfg.general_settings.provider_discovery_allow_http,
            allowed_ports=cfg.general_settings.provider_discovery_allowed_ports,
            allowed_private_cidrs=cfg.general_settings.provider_discovery_allowed_private_cidrs,
            resolution_timeout_seconds=2.0,
        ),
    )
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
        compatible_chat={
            name: ProfiledChatAdapter(http_client, profile)
            for name, profile in CHAT_PROVIDER_PROFILES.items()
        },
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
        bulk_redis_client=bulk_redis_client,
        cache_redis_client=cache_redis_client,
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
    if runtime.cleanup is not None:
        await runtime.cleanup.aclose()
