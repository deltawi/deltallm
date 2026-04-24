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
from src.db.repositories import ModelDeploymentRecord
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


class InMemoryModelRepository:
    def __init__(self, records: list[ModelDeploymentRecord] | None = None) -> None:
        self.records = records or []

    async def list_all(self) -> list[ModelDeploymentRecord]:
        return list(self.records)

    async def create(self, record: ModelDeploymentRecord) -> ModelDeploymentRecord:
        self.records.append(record)
        return record

    async def update(
        self,
        deployment_id: str,
        *,
        model_name: str,
        deltallm_params: dict[str, Any],
        model_info: dict[str, Any] | None,
    ) -> ModelDeploymentRecord | None:
        for idx, record in enumerate(self.records):
            if record.deployment_id == deployment_id:
                updated = ModelDeploymentRecord(
                    deployment_id=deployment_id,
                    model_name=model_name,
                    deltallm_params=deltallm_params,
                    model_info=model_info,
                )
                self.records[idx] = updated
                return updated
        return None

    async def delete(self, deployment_id: str) -> bool:
        original_len = len(self.records)
        self.records = [item for item in self.records if item.deployment_id != deployment_id]
        return len(self.records) < original_len


class FakeRouteGroupCache:
    def __init__(self) -> None:
        self.invalidate_calls = 0

    async def invalidate(self) -> None:
        self.invalidate_calls += 1


@pytest.mark.asyncio
async def test_dynamic_config_merges_db_and_notifies_subscribers(monkeypatch):
    monkeypatch.setenv("MASTER", "ResolvedMasterKey2026SecureValue123")
    monkeypatch.setenv("OPENAI_API_KEY", "resolved-openai-key")
    db = FakeDB(
        {
            "general_settings": {"master_key": "os.environ/MASTER"},
            "model_list": [
                {
                    "model_name": "gpt-4o-mini",
                    "deployment_id": "dep-1",
                    "deltallm_params": {
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
        file_config={"general_settings": {"master_key": "FallbackMasterKey2026SecureValue99"}},
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
    assert cfg.general_settings.master_key == "ResolvedMasterKey2026SecureValue123"
    assert cfg.model_list[0].deployment_id == "dep-1"
    assert cfg.model_list[0].deltallm_params.api_key == "resolved-openai-key"
    assert cfg.router_settings.routing_strategy == "weighted"
    assert called

    await manager.close()


@pytest.mark.asyncio
async def test_dynamic_config_model_updated_notifies_subscribers_without_config_diff():
    redis = FakeRedis()
    manager = DynamicConfigManager(db_client=FakeDB(), redis_client=redis, file_config={})

    called: list[dict[str, list[str]]] = []

    async def on_change(new_config, changes):
        del new_config
        called.append(changes)

    manager.subscribe(on_change)
    await manager.initialize()

    await redis.pubsub_obj.queue.put(
        {
            "type": "message",
            "data": json.dumps({"type": "model_updated"}),
        }
    )
    await asyncio.sleep(0.05)

    assert called == [{"added": [], "removed": [], "modified": ["model_list"]}]

    await manager.close()


@pytest.mark.asyncio
async def test_dynamic_config_config_updated_without_diff_is_noop():
    redis = FakeRedis()
    manager = DynamicConfigManager(db_client=FakeDB(), redis_client=redis, file_config={})

    called: list[dict[str, list[str]]] = []

    async def on_change(new_config, changes):
        del new_config
        called.append(changes)

    manager.subscribe(on_change)
    await manager.initialize()

    await redis.pubsub_obj.queue.put(
        {
            "type": "message",
            "data": json.dumps({"type": "config_updated"}),
        }
    )
    await asyncio.sleep(0.05)

    assert called == []

    await manager.close()


@pytest.mark.asyncio
async def test_model_hot_reload_manager_updates_runtime_registries():
    settings = SimpleNamespace(
        openai_api_key="provider-key",
        openai_base_url="https://api.openai.com/v1",
        salt_key="test-salt",
    )
    initial_model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": "old-dep",
                "deltallm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"},
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
                    "deltallm_params": {"model": "openai/gpt-4.1-mini", "api_key": "provider-key"},
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


@pytest.mark.asyncio
async def test_model_hot_reload_manager_model_crud_refreshes_runtime_registry():
    settings = SimpleNamespace(
        openai_api_key="provider-key",
        openai_base_url="https://api.openai.com/v1",
        salt_key="test-salt",
    )
    initial_model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": "old-dep",
                "deltallm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"},
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
            app_config=AppConfig.model_validate({}),
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
    repo = InMemoryModelRepository(
        records=[
            ModelDeploymentRecord(
                deployment_id="old-dep",
                model_name="gpt-4o-mini",
                deltallm_params={"model": "openai/gpt-4o-mini"},
                model_info={},
            )
        ]
    )
    route_group_cache = FakeRouteGroupCache()
    manager = ModelHotReloadManager(
        app=app,
        dynamic_config=dynamic,
        model_repository=repo,
        route_group_cache=route_group_cache,
    )

    new_id = await manager.add_model(
        {
            "model_name": "gpt-4.1-mini",
            "deployment_id": "new-dep",
            "deltallm_params": {"model": "openai/gpt-4.1-mini"},
            "model_info": {"weight": 2},
        }
    )
    assert new_id == "new-dep"
    assert "gpt-4.1-mini" in app.state.model_registry

    updated = await manager.update_model(
        "new-dep",
        {
            "model_name": "gpt-4.1-mini",
            "deltallm_params": {"model": "openai/gpt-4.1-mini"},
            "model_info": {"weight": 4},
        },
    )
    assert updated is True
    assert app.state.model_registry["gpt-4.1-mini"][0]["model_info"]["weight"] == 4

    removed = await manager.remove_model("new-dep")
    assert removed is True
    assert "gpt-4.1-mini" not in app.state.model_registry
    assert route_group_cache.invalidate_calls == 3

    await dynamic.close()


@pytest.mark.asyncio
async def test_model_hot_reload_manager_reloads_runtime_on_model_updated_event():
    settings = SimpleNamespace(
        openai_api_key="provider-key",
        openai_base_url="https://api.openai.com/v1",
        salt_key="test-salt",
    )
    initial_model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": "old-dep",
                "deltallm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"},
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
            app_config=AppConfig.model_validate({}),
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
    redis = FakeRedis()
    dynamic = DynamicConfigManager(db_client=FakeDB(), redis_client=redis, file_config={})
    await dynamic.initialize()
    repo = InMemoryModelRepository(
        records=[
            ModelDeploymentRecord(
                deployment_id="old-dep",
                model_name="gpt-4o-mini",
                deltallm_params={"model": "openai/gpt-4o-mini"},
                model_info={},
            )
        ]
    )
    ModelHotReloadManager(
        app=app,
        dynamic_config=dynamic,
        model_repository=repo,
        route_group_cache=FakeRouteGroupCache(),
    )

    repo.records.append(
        ModelDeploymentRecord(
            deployment_id="new-dep",
            model_name="gpt-4.1-mini",
            deltallm_params={"model": "openai/gpt-4.1-mini"},
            model_info={"weight": 2},
        )
    )

    await redis.pubsub_obj.queue.put(
        {
            "type": "message",
            "data": json.dumps({"type": "model_updated"}),
        }
    )
    await asyncio.sleep(0.05)

    assert "gpt-4.1-mini" in app.state.model_registry
    assert app.state.router.deployment_registry["gpt-4.1-mini"][0].deployment_id == "new-dep"

    await dynamic.close()


@pytest.mark.asyncio
async def test_model_hot_reload_manager_rejects_duplicate_model_name() -> None:
    settings = SimpleNamespace(
        openai_api_key="default-key",
        openai_base_url="https://api.openai.com/v1",
        salt_key="test-salt",
    )
    initial_model_registry = {
        "gpt-4o-mini": [{"deployment_id": "old-dep", "deltallm_params": {"model": "openai/gpt-4o-mini"}, "model_info": {}}]
    }
    deployment_registry = build_deployment_registry(initial_model_registry)
    state_backend = RedisStateBackend(redis=None)
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
            app_config=AppConfig.model_validate({}),
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
    manager = ModelHotReloadManager(
        app=app,
        dynamic_config=dynamic,
        model_repository=None,
        route_group_cache=FakeRouteGroupCache(),
    )

    with pytest.raises(ValueError, match="Duplicate model_name 'gpt-4o-mini' is not allowed"):
        await manager.add_model(
            {
                "model_name": "gpt-4o-mini",
                "deployment_id": "new-dep",
                "deltallm_params": {"model": "azure/gpt-4o-mini"},
                "model_info": {"weight": 2},
            }
        )

    await dynamic.close()
