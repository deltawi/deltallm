from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from redis.asyncio import Redis

from src.bootstrap.status import BootstrapStatus
from src.batch import BatchRepository
from src.config import get_settings, resolve_database_settings, resolve_salt_key
from src.config_runtime import DynamicConfigManager, SecretResolver, build_app_config, load_yaml_dict
from src.db.callable_targets import CallableTargetBindingRepository
from src.db.callable_target_policies import CallableTargetScopePolicyRepository
from src.db.client import prisma_manager
from src.db.mcp import MCPRepository
from src.db.mcp_scope_policies import MCPScopePolicyRepository
from src.db.prompt_registry import PromptRegistryRepository
from src.db.repositories import ModelDeploymentRepository
from src.db.route_groups import RouteGroupRepository
from src.providers.anthropic import AnthropicAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.openai import OpenAIAdapter
from src.services.route_groups import RouteGroupRuntimeCache


@dataclass
class InfrastructureRuntime:
    redis_client: Redis | None
    dynamic_config_manager: DynamicConfigManager
    http_client: httpx.AsyncClient
    statuses: tuple[BootstrapStatus, ...] = ()


def _build_redis_client(settings: Any, cfg: Any) -> Redis:
    redis_url = settings.redis_url or cfg.general_settings.redis_url
    if redis_url:
        return Redis.from_url(redis_url, decode_responses=True)

    host = cfg.general_settings.redis_host or settings.redis_host
    port = cfg.general_settings.redis_port or settings.redis_port
    password = cfg.general_settings.redis_password or settings.redis_password
    return Redis(host=host, port=port, password=password, decode_responses=True)


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

    http_client = httpx.AsyncClient(timeout=60)
    app.state.http_client = http_client
    app.state.openai_adapter = OpenAIAdapter(http_client)
    app.state.azure_openai_adapter = AzureOpenAIAdapter(http_client)
    app.state.anthropic_adapter = AnthropicAdapter(http_client)
    app.state.gemini_adapter = GeminiAdapter(http_client)
    app.state.bedrock_adapter = BedrockAdapter(http_client)

    app.state.model_deployment_repository = ModelDeploymentRepository(prisma_manager.client)
    app.state.callable_target_binding_repository = CallableTargetBindingRepository(prisma_manager.client)
    app.state.callable_target_scope_policy_repository = CallableTargetScopePolicyRepository(prisma_manager.client)
    app.state.route_group_repository = RouteGroupRepository(prisma_manager.client)
    app.state.prompt_registry_repository = PromptRegistryRepository(prisma_manager.client)
    app.state.mcp_repository = MCPRepository(prisma_manager.client)
    app.state.mcp_scope_policy_repository = MCPScopePolicyRepository(prisma_manager.client)
    app.state.batch_repository = BatchRepository(prisma_manager.client)

    return InfrastructureRuntime(
        redis_client=redis_client,
        dynamic_config_manager=dynamic_config_manager,
        http_client=http_client,
        statuses=(
            BootstrapStatus("config", "ready"),
            BootstrapStatus("redis", "ready"),
            BootstrapStatus("database", "ready"),
            BootstrapStatus("dynamic_config", "ready"),
            BootstrapStatus("http_client", "ready"),
            BootstrapStatus("provider_adapters", "ready"),
        ),
    )


async def shutdown_infrastructure_runtime(runtime: InfrastructureRuntime) -> None:
    await runtime.dynamic_config_manager.close()
    await runtime.http_client.aclose()
    if runtime.redis_client is not None:
        await runtime.redis_client.close()
    await prisma_manager.disconnect()
