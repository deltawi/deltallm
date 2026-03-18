from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.bootstrap.infrastructure import init_infrastructure_runtime, shutdown_infrastructure_runtime


@pytest.mark.asyncio
async def test_init_and_shutdown_infrastructure_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeDynamicConfigManager:
        def __init__(self, *, db_client, redis_client, file_config) -> None:  # noqa: ANN001
            self.db_client = db_client
            self.redis_client = redis_client
            self.file_config = file_config
            self.closed = False

        async def initialize(self) -> None:
            created["dynamic_initialized"] = True

        def get_app_config(self):  # noqa: ANN201
            return SimpleNamespace(general_settings=SimpleNamespace(), deltallm_settings=SimpleNamespace())

        async def close(self) -> None:
            self.closed = True

    class FakeHTTPClient:
        def __init__(self, timeout) -> None:  # noqa: ANN001
            self.timeout = timeout
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    class FakeRedis:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.closed = False

        @classmethod
        def from_url(cls, url: str, decode_responses: bool = True):  # noqa: FBT001, FBT002
            instance = cls(url=url, decode_responses=decode_responses)
            created["redis"] = instance
            return instance

        async def close(self) -> None:
            self.closed = True

    class FakePrismaManager:
        def __init__(self) -> None:
            self.client = "db-client"
            self.connected = False
            self.disconnected = False
            self.database_settings = None

        async def connect(self, database_settings=None) -> None:  # noqa: ANN001
            self.connected = True
            self.database_settings = database_settings

        async def disconnect(self) -> None:
            self.disconnected = True

    monkeypatch.setattr(
        "src.bootstrap.infrastructure.get_settings",
        lambda: SimpleNamespace(
            config_path="config.yaml",
            database_url="postgresql://env-user:env-pass@env-host:5432/env-db",
            db_pool_size=25,
            db_pool_timeout=45,
            redis_url="redis://localhost:6379/0",
            redis_host="localhost",
            redis_port=6379,
            redis_password=None,
        ),
    )
    monkeypatch.setattr("src.bootstrap.infrastructure.load_yaml_dict", lambda path: {"loaded_from": path})
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.build_app_config",
        lambda file_config, secret_resolver: SimpleNamespace(  # noqa: ARG005
            general_settings=SimpleNamespace(
                database_url="postgresql://cfg-user:cfg-pass@cfg-host:5432/cfg-db?schema=public",
                db_pool_size=20,
                db_pool_timeout=30,
                redis_url=None,
                redis_host="localhost",
                redis_port=6379,
                redis_password=None,
            ),
            deltallm_settings=SimpleNamespace(),
        ),
    )
    monkeypatch.setattr("src.bootstrap.infrastructure.DynamicConfigManager", FakeDynamicConfigManager)
    monkeypatch.setattr("src.bootstrap.infrastructure.Redis", FakeRedis)
    monkeypatch.setattr("src.bootstrap.infrastructure.prisma_manager", FakePrismaManager())
    monkeypatch.setattr("src.bootstrap.infrastructure.resolve_salt_key", lambda cfg, settings: "salt")  # noqa: ARG005
    monkeypatch.setattr("src.bootstrap.infrastructure.httpx.AsyncClient", FakeHTTPClient)
    monkeypatch.setattr("src.bootstrap.infrastructure.OpenAIAdapter", lambda client: ("openai", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.AzureOpenAIAdapter", lambda client: ("azure", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.AnthropicAdapter", lambda client: ("anthropic", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.GeminiAdapter", lambda client: ("gemini", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.BedrockAdapter", lambda client: ("bedrock", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.RouteGroupRuntimeCache", lambda redis_client: ("route-cache", redis_client))
    monkeypatch.setattr("src.bootstrap.infrastructure.ModelDeploymentRepository", lambda client: ("model-repo", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.RouteGroupRepository", lambda client: ("route-group-repo", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.PromptRegistryRepository", lambda client: ("prompt-repo", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.MCPRepository", lambda client: ("mcp-repo", client))
    monkeypatch.setattr("src.bootstrap.infrastructure.BatchRepository", lambda client: ("batch-repo", client))

    app = SimpleNamespace(state=SimpleNamespace())

    runtime = await init_infrastructure_runtime(app)

    assert app.state.settings.config_path == "config.yaml"
    assert app.state.redis is created["redis"]
    assert app.state.route_group_runtime_cache == ("route-cache", created["redis"])
    assert app.state.prisma_manager.client == "db-client"
    assert app.state.prisma_manager.database_settings is not None
    assert app.state.prisma_manager.database_settings.pool_size == 25
    assert app.state.prisma_manager.database_settings.pool_timeout == 45
    assert app.state.prisma_manager.database_settings.url == (
        "postgresql://env-user:env-pass@env-host:5432/env-db"
        "?connection_limit=25&pool_timeout=45"
    )
    assert app.state.dynamic_config_manager is runtime.dynamic_config_manager
    assert app.state.salt_key == "salt"
    assert app.state.openai_adapter[0] == "openai"
    assert app.state.batch_repository == ("batch-repo", "db-client")

    await shutdown_infrastructure_runtime(runtime)

    assert runtime.dynamic_config_manager.closed is True
    assert runtime.http_client.closed is True
    assert runtime.redis_client.closed is True
    assert app.state.prisma_manager.disconnected is True
