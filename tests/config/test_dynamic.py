from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import AppConfig, RouterSettings
from src.config_runtime.dynamic import DynamicConfigManager
from src.config_runtime.models import ModelHotReloadManager
from src.config_runtime.secrets import BaseSecretManager, SecretResolver
from src.router import (
    CooldownManager,
    FailoverManager,
    FallbackConfig,
    HealthCheckConfig,
    HealthEndpointHandler,
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
)
from src.router.health import BackgroundHealthChecker


class StaticSecretManager(BaseSecretManager):
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get_secret(self, path: str) -> str | None:
        return self.values.get(path)


class FakePubSub:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        del channel

    async def listen(self):
        while True:
            item = await self.queue.get()
            if item.get("type") == "stop":
                break
            yield item

    async def unsubscribe(self, channel: str) -> None:
        del channel

    async def close(self) -> None:
        await self.queue.put({"type": "stop"})


class FakeRedis:
    def __init__(self) -> None:
        self.pubsub_obj = FakePubSub()
        self.messages: list[tuple[str, str]] = []

    def pubsub(self) -> FakePubSub:
        return self.pubsub_obj

    async def publish(self, channel: str, payload: str) -> None:
        self.messages.append((channel, payload))


class FakeDB:
    def __init__(self, config_value: dict[str, Any] | None = None) -> None:
        self.config_value = config_value or {}
        self.updated_by: str | None = None

    async def query_raw(self, query: str, name: str):
        del query, name
        return [{"config_value": json.dumps(self.config_value)}]

    async def execute_raw(self, query: str, name: str, payload: str, updated_by: str):
        del query, name
        self.config_value = json.loads(payload)
        self.updated_by = updated_by


@pytest.mark.asyncio
async def test_dynamic_config_merges_db_and_notifies_subscribers():
    db = FakeDB(
        {
            "general_settings": {"master_key": "os.environ/MASTER"},
            "model_list": [
                {
                    "model_name": "gpt-4o-mini",
                    "deployment_id": "dep-1",
                    "litellm_params": {
                        "model": "openai/gpt-4o-mini",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                }
            ],
        }
    )
    redis = FakeRedis()
    resolver = SecretResolver(
        aws=StaticSecretManager({}),
        gcp=StaticSecretManager({}),
        azure=StaticSecretManager({}),
    )

    manager = DynamicConfigManager(
        db_client=db,
        redis_client=redis,
        file_config={"general_settings": {"master_key": "fallback"}},
        secret_resolver=resolver,
    )

    called: list[dict[str, list[str]]] = []

    async def on_change(new_config, changes):
        del new_config
        called.append(changes)

    manager.subscribe(on_change)
    await manager.initialize()

    db.config_value["router_settings"] = RouterSettings(routing_strategy="weighted").model_dump()
    await redis.pubsub_obj.queue.put(
        {
            "type": "message",
            "data": json.dumps({"type": "config_updated"}),
        }
    )
    await asyncio.sleep(0.05)

    cfg = manager.get_app_config()
    assert cfg.general_settings.master_key == "os.environ/MASTER"
    assert cfg.model_list[0].deployment_id == "dep-1"
    assert cfg.router_settings.routing_strategy == "weighted"
    assert called

    await manager.close()


@pytest.mark.asyncio
async def test_model_hot_reload_manager_updates_runtime_registries():
    settings = SimpleNamespace(openai_api_key="provider-key", openai_base_url="https://api.openai.com/v1")
    initial_model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": "old-dep",
                "litellm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"},
                "model_info": {},
            }
        ]
    }
    deployment_registry = build_deployment_registry(initial_model_registry)

    state_backend = RedisStateBackend(None)
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state_backend,
        config=RouterConfig(),
        deployment_registry=deployment_registry,
    )
    cooldown_manager = CooldownManager(state_backend=state_backend)
    failover_manager = FailoverManager(
        config=FallbackConfig(),
        deployment_registry=deployment_registry,
        state_backend=state_backend,
        cooldown_manager=cooldown_manager,
    )
    health_handler = HealthEndpointHandler(deployment_registry=deployment_registry, state_backend=state_backend)
    health_checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=False),
        deployment_registry=deployment_registry,
        state_backend=state_backend,
        checker=lambda _: asyncio.sleep(0, result=True),
    )

    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            app_config=None,
            model_registry=initial_model_registry,
            router=router,
            failover_manager=failover_manager,
            router_health_handler=health_handler,
            background_health_checker=health_checker,
            cooldown_manager=cooldown_manager,
            guardrail_registry=SimpleNamespace(load_from_config=lambda _: None),
            callback_manager=SimpleNamespace(load_from_settings=lambda **_: None),
            turn_off_message_logging=False,
        )
    )

    dynamic = DynamicConfigManager(db_client=FakeDB(), redis_client=None, file_config={})
    await dynamic.initialize()

    manager = ModelHotReloadManager(app=app, dynamic_config=dynamic)

    updated_cfg = AppConfig.model_validate(
        {
            **dynamic.get_app_config().model_dump(mode="python"),
            "model_list": [
                {
                    "model_name": "gpt-4.1-mini",
                    "deployment_id": "new-dep",
                    "litellm_params": {"model": "openai/gpt-4.1-mini", "api_key": "provider-key"},
                }
            ],
            "router_settings": {"routing_strategy": "weighted", "num_retries": 2},
        }
    )

    await manager._on_config_change(updated_cfg, {"added": [], "removed": [], "modified": ["model_list", "router_settings"]})

    assert "gpt-4.1-mini" in app.state.model_registry
    assert app.state.router.strategy == RoutingStrategy.WEIGHTED
    assert app.state.router.config.num_retries == 2
    assert app.state.failover_manager.config.num_retries == 2
    assert app.state.router.deployment_registry["gpt-4.1-mini"][0].deployment_id == "new-dep"

    await dynamic.close()
