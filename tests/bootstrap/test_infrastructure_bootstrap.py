from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.bootstrap.infrastructure import (
    _startup_setting,
    init_infrastructure_runtime,
    shutdown_infrastructure_runtime,
)
from src.config import GeneralSettings, Settings
from src.config_runtime.loader import build_app_config
from src.providers.error_body import bound_provider_error_response_body


def test_telemetry_startup_mode_uses_env_only_when_config_is_implicit() -> None:
    settings = Settings.model_validate({"audit_ingestion_mode": "outbox"})

    assert (
        _startup_setting(GeneralSettings(), settings, "audit_ingestion_mode", "legacy") == "outbox"
    )
    assert (
        _startup_setting(
            GeneralSettings.model_validate({"audit_ingestion_mode": "legacy"}),
            settings,
            "audit_ingestion_mode",
            "legacy",
        )
        == "legacy"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_startup", [False, True])
@pytest.mark.parametrize(
    "durable,operations,accounting",
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (False, False, True),
        (True, True, True),
    ],
)
async def test_init_and_shutdown_infrastructure_runtime(
    monkeypatch: pytest.MonkeyPatch, fail_startup, durable, operations, accounting
) -> None:
    created: dict[str, object] = {}
    telemetry_required = durable or operations or accounting
    telemetry_worker_required = durable or operations

    class FakeDynamicConfigManager:
        def __init__(self, *, db_client, redis_client, file_config, defer_updates) -> None:  # noqa: ANN001
            self.db_client = db_client
            self.redis_client = redis_client
            self.file_config = file_config
            self.closed = False
            created["dynamic"] = self
            self.subscribers = []

        def attach_redis(self, redis_client) -> None:
            self.redis_client = redis_client

        async def initialize(self) -> None:
            assert self.redis_client is None
            created["dynamic_initialized"] = True

        def get_app_config(self):  # noqa: ANN201
            return SimpleNamespace(
                general_settings=GeneralSettings(
                    audit_ingestion_mode="outbox" if durable else "legacy",
                    spend_ingestion_mode="outbox" if operations else "legacy",
                    spend_operation_intents_enabled=operations,
                    accounting_protocol_enabled=accounting,
                    provider_discovery_allow_http=False,
                    provider_discovery_allowed_ports=[443],
                    provider_discovery_allowed_private_cidrs=[],
                    upstream_http_connect_timeout_seconds=7,
                    upstream_http_read_timeout_seconds=301,
                    upstream_http_write_timeout_seconds=33,
                    upstream_http_pool_timeout_seconds=4,
                    upstream_http_max_connections=123,
                    upstream_http_max_keepalive_connections=45,
                    upstream_http_keepalive_expiry_seconds=12,
                ),
                deltallm_settings=SimpleNamespace(),
            )

        def subscribe(self, callback) -> None:  # noqa: ANN001
            self.subscribers.append(callback)

        async def close(self) -> None:
            self.closed = True

    class FakeHTTPClient:
        def __init__(
            self,
            *,
            timeout,
            limits,
            event_hooks=None,
            transport=None,
            http1=True,
            http2=False,
            mounts=None,
        ) -> None:  # noqa: ANN001
            self.transport = transport
            self.timeout = timeout
            self.limits = limits
            self.event_hooks = event_hooks
            self.closed = False
            created.setdefault("http_clients", []).append(self)

        async def aclose(self) -> None:
            self.closed = True
            if self.transport is not None:
                await self.transport.aclose()

    class FakeRedis:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.closed = False

        @classmethod
        def from_url(cls, url: str, decode_responses: bool = True):  # noqa: FBT001, FBT002
            instance = cls(url=url, decode_responses=decode_responses)
            created["critical_redis"] = instance
            return instance

        async def aclose(self) -> None:
            self.closed = True

    class FakePrismaManager:
        def __init__(self) -> None:
            self.client = "db-client"
            self.connected = False
            self.disconnected = False
            self.database_settings = None

        async def connect(self, database_settings=None, *, policy=None) -> None:  # noqa: ANN001
            self.connected = True
            self.database_settings = database_settings
            self.policy = policy

        async def disconnect(self) -> None:
            self.disconnected = True

    class FakeAccountingManager:
        def __init__(self) -> None:
            self.client = "accounting-db-client"
            self.connected = False
            self.disconnected = False
            self.kwargs = None

        async def connect(self, database_settings, **kwargs) -> None:  # noqa: ANN001
            self.connected = True
            self.database_settings = database_settings
            self.kwargs = kwargs

        async def disconnect(self) -> None:
            self.disconnected = True

    class FakeUIBrandingAssetService:
        def __init__(self, db_client) -> None:  # noqa: ANN001
            self.db_client = db_client
            self.initialized_with = None

        async def initialize(self, cfg) -> None:  # noqa: ANN001
            self.initialized_with = cfg
            if fail_startup:
                raise RuntimeError("branding startup failed")

        async def on_config_change(self, cfg, changes) -> None:  # noqa: ANN001
            del cfg, changes

    class FakeCreatorModelAccessService:
        def __init__(self, access_repository, logical_model_repository, **kwargs) -> None:  # noqa: ANN001, ANN003
            self.access_repository = access_repository
            self.logical_model_repository = logical_model_repository
            self.kwargs = kwargs
            self.reloaded = False

        async def reload(self) -> None:
            self.reloaded = True

    class FakeCreatorPromptAccessService:
        def __init__(self, access_repository, prompt_repository, **kwargs) -> None:  # noqa: ANN001, ANN003
            self.access_repository = access_repository
            self.prompt_repository = prompt_repository
            self.kwargs = kwargs
            self.reloaded = False

        async def reload(self) -> None:
            self.reloaded = True

    class FakeCreatorRouteGroupAccessService:
        def __init__(self, access_repository, route_group_repository, **kwargs) -> None:  # noqa: ANN001, ANN003
            self.access_repository = access_repository
            self.route_group_repository = route_group_repository
            self.kwargs = kwargs
            self.reloaded = False

        async def reload(self) -> None:
            self.reloaded = True

    class FakeCreatorMCPAccessService:
        def __init__(self, access_repository, mcp_repository, **kwargs) -> None:  # noqa: ANN001, ANN003
            self.access_repository = access_repository
            self.mcp_repository = mcp_repository
            self.kwargs = kwargs
            self.reloaded = False

        async def reload(self) -> None:
            self.reloaded = True

    class FakeManagedAssetReconciliationService:
        def __init__(self, repository, **kwargs) -> None:  # noqa: ANN001
            self.repository = repository
            self.kwargs = kwargs
            self.started = False
            self.closed = False

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

        def health_snapshot(self):  # noqa: ANN201
            return SimpleNamespace(ready=True, detail=None)

    monkeypatch.setattr(
        "src.startup_config.get_settings",
        lambda: Settings(
            app_env="test",
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
    monkeypatch.setattr("src.startup_config.load_yaml_dict", lambda path: {"loaded_from": path})
    monkeypatch.setattr(
        "src.startup_config.build_app_config",
        lambda file_config, secret_resolver: SimpleNamespace(  # noqa: ARG005
            general_settings=GeneralSettings(
                audit_ingestion_mode="outbox" if durable else "legacy",
                spend_ingestion_mode="outbox" if operations else "legacy",
                spend_operation_intents_enabled=operations,
                accounting_protocol_enabled=accounting,
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
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.DynamicConfigManager", FakeDynamicConfigManager
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.UIBrandingAssetService", FakeUIBrandingAssetService
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.CreatorModelAccessService",
        FakeCreatorModelAccessService,
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.CreatorPromptAccessService",
        FakeCreatorPromptAccessService,
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.CreatorRouteGroupAccessService",
        FakeCreatorRouteGroupAccessService,
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.CreatorMCPAccessService",
        FakeCreatorMCPAccessService,
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.ManagedAssetReconciliationService",
        FakeManagedAssetReconciliationService,
    )

    def build_redis(settings, general, *, allocation, endpoint_settings):
        client = FakeRedis(allocation=allocation)
        created[allocation + "_redis"] = client
        return client

    monkeypatch.setattr("src.bootstrap.infrastructure.build_redis_client", build_redis)
    monkeypatch.setattr("src.bootstrap.infrastructure.prisma_manager", FakePrismaManager())
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.foreground_prisma_manager", FakePrismaManager()
    )
    for name in (
        "telemetry_prisma_manager",
        "telemetry_worker_prisma_manager",
        "telemetry_settlement_prisma_manager",
    ):
        monkeypatch.setattr("src.bootstrap.infrastructure." + name, FakePrismaManager())
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.accounting_postgres_manager",
        FakeAccountingManager(),
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.resolve_salt_key", lambda cfg, settings: "salt"
    )  # noqa: ARG005
    monkeypatch.setattr("src.upstream_http.httpx.AsyncClient", FakeHTTPClient)
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.OpenAIAdapter", lambda client: ("openai", client)
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.AzureOpenAIAdapter", lambda client: ("azure", client)
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.AnthropicAdapter", lambda client: ("anthropic", client)
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.GeminiAdapter", lambda client: ("gemini", client)
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.BedrockAdapter", lambda client: ("bedrock", client)
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.RouteGroupRuntimeCache",
        lambda redis_client, keyspace: ("route-cache", redis_client, keyspace),
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.ModelDeploymentRepository",
        lambda client: ("model-repo", client),
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.RouteGroupRepository",
        lambda client: ("route-group-repo", client),
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.PromptRegistryRepository",
        lambda client: ("prompt-repo", client),
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.MCPRepository", lambda client: ("mcp-repo", client)
    )
    monkeypatch.setattr(
        "src.bootstrap.infrastructure.BatchRepository",
        lambda client, **kwargs: ("batch-repo", client, kwargs),
    )

    app = SimpleNamespace(state=SimpleNamespace())

    if fail_startup:
        with pytest.raises(RuntimeError, match="branding startup failed"):
            await init_infrastructure_runtime(app)
        assert created["critical_redis"].closed
        assert created["cache_redis"].closed
        assert app.state.foreground_prisma_manager.disconnected
        assert app.state.telemetry_prisma_manager.disconnected is telemetry_required
        assert app.state.telemetry_worker_prisma_manager.disconnected is telemetry_worker_required
        assert app.state.telemetry_settlement_prisma_manager.disconnected is (
            operations and not accounting
        )
        assert app.state.accounting_postgres_manager.disconnected is accounting
        assert created["bulk_redis"].closed
        assert created["dynamic"].closed
        assert app.state.prisma_manager.disconnected
        return
    runtime = await init_infrastructure_runtime(app)

    assert app.state.settings.config_path == "config.yaml"
    assert app.state.redis is created["critical_redis"]
    route_cache, route_cache_redis, route_cache_keyspace = app.state.route_group_runtime_cache
    assert route_cache == "route-cache"
    assert route_cache_redis is created["cache_redis"]
    assert route_cache_keyspace.environment == "test"
    assert app.state.prisma_manager.client == "db-client"
    assert app.state.prisma_manager.database_settings is not None
    assert app.state.prisma_manager.database_settings.pool_size == 25
    assert app.state.prisma_manager.database_settings.pool_timeout == 45
    assert app.state.prisma_manager.database_settings.url == (
        "postgresql://env-user:env-pass@env-host:5432/env-db?connection_limit=25&pool_timeout=45"
    )
    assert app.state.dynamic_config_manager is runtime.dynamic_config_manager
    assert app.state.telemetry_worker_database_required is telemetry_worker_required
    assert app.state.ui_branding_asset_service.db_client == "db-client"
    assert app.state.ui_branding_asset_service.initialized_with is app.state.app_config
    assert app.state.dynamic_config_manager.subscribers == [
        app.state.ui_branding_asset_service.on_config_change
    ]
    assert app.state.salt_key == "salt"
    assert runtime.http_client.timeout.connect == 7
    assert runtime.http_client.timeout.read == 301
    assert runtime.http_client.timeout.write == 33
    assert runtime.http_client.timeout.pool == 4
    assert runtime.http_client.limits.max_connections == 123
    assert runtime.http_client.limits.max_keepalive_connections == 45
    assert runtime.http_client.limits.keepalive_expiry == 12
    assert runtime.http_client.event_hooks == {"response": [bound_provider_error_response_body]}
    assert app.state.control_http_client is runtime.control_http_client
    assert runtime.control_http_client is not runtime.http_client
    assert app.state.provider_discovery_runtime.transport is runtime.control_http_client.transport
    assert runtime.control_http_client.timeout.connect == 5
    assert runtime.control_http_client.timeout.read == 20
    assert runtime.control_http_client.timeout.write == 10
    assert runtime.control_http_client.timeout.pool == 5
    assert runtime.control_http_client.limits.max_connections == 100
    assert runtime.control_http_client.limits.max_keepalive_connections == 0
    assert runtime.control_http_client.limits.keepalive_expiry == 30
    assert runtime.control_http_client.event_hooks is None
    assert app.state.openai_adapter[0] == "openai"
    assert app.state.batch_repository == (
        "batch-repo",
        "db-client",
        {"webhook_max_attempts": 8},
    )
    assert runtime.managed_asset_reconciliation_service.started is True

    await shutdown_infrastructure_runtime(runtime)

    assert runtime.managed_asset_reconciliation_service.closed is True
    assert runtime.dynamic_config_manager.closed is True
    assert runtime.http_client.closed is True
    assert runtime.control_http_client.closed is True
    assert runtime.redis_client.closed is True
    assert runtime.cache_redis_client.closed is True
    assert app.state.foreground_prisma_manager.disconnected
    assert app.state.foreground_prisma_manager.policy.allocation == "foreground"
    assert app.state.foreground_prisma_manager.policy.connections == 8
    for name, allocation in (
        ("telemetry_prisma_manager", "telemetry"),
        ("telemetry_worker_prisma_manager", "telemetry_worker"),
    ):
        manager = getattr(app.state, name)
        required = telemetry_required if allocation == "telemetry" else telemetry_worker_required
        assert manager.connected is required
        assert manager.disconnected is required
        if required:
            assert manager.policy.allocation == allocation
            assert manager.policy.connections == (
                4
                if operations and not accounting and allocation == "telemetry"
                else 3
                if accounting and allocation == "telemetry"
                else 5
            )
    legacy_operations = operations and not accounting
    assert app.state.telemetry_settlement_prisma_manager.connected is legacy_operations
    assert app.state.telemetry_settlement_prisma_manager.disconnected is legacy_operations
    if legacy_operations:
        assert app.state.telemetry_settlement_prisma_manager.policy.connections == 1
    assert app.state.accounting_postgres_manager.connected is accounting
    assert app.state.accounting_postgres_manager.disconnected is accounting
    if accounting:
        assert app.state.accounting_postgres_manager.kwargs["pool_size"] == 2
    assert runtime.bulk_redis_client.closed is True
    assert app.state.bulk_redis is runtime.bulk_redis_client
    assert runtime.bulk_redis_client is not runtime.redis_client
    assert runtime.dynamic_config_manager.redis_client is runtime.redis_client
    assert app.state.prisma_manager.disconnected is True


@pytest.mark.parametrize(
    "durable_override",
    [
        {"redis_critical_max_connections": 10000},
        {"redis_cache_max_connections": 10000},
        {"redis_bulk_max_connections": 10000},
        {"redis_acquisition_timeout_seconds": 30},
        {"redis_socket_timeout_seconds": 30},
        {"redis_connect_timeout_seconds": 30},
        {"audit_ingestion_mode": "outbox"},
        {"spend_ingestion_mode": "outbox"},
        {"telemetry_db_pool_size": 100},
    ],
)
async def test_durable_config_cannot_expand_startup_dependency_budget(
    monkeypatch, durable_override
):
    from src.bootstrap import infrastructure

    settings = Settings(database_url="postgresql://fixture:fixture@fixture/db")
    initial = {"general_settings": {"audit_ingestion_mode": "legacy"}}
    if "telemetry_db_pool_size" in durable_override:
        initial["general_settings"]["audit_ingestion_mode"] = "outbox"
    effective = build_app_config(initial, {"general_settings": durable_override})
    dynamic = SimpleNamespace(
        initialize=AsyncMock(), get_app_config=lambda: effective, close=AsyncMock()
    )
    managers = {}
    for name in (
        "prisma_manager",
        "foreground_prisma_manager",
        "telemetry_prisma_manager",
        "telemetry_worker_prisma_manager",
    ):
        managers[name] = SimpleNamespace(
            client=object(), connect=AsyncMock(), disconnect=AsyncMock()
        )
        monkeypatch.setattr(infrastructure, name, managers[name])
    from src import startup_config

    monkeypatch.setattr(startup_config, "get_settings", lambda: settings)
    monkeypatch.setattr(startup_config, "load_yaml_dict", lambda _: initial)
    monkeypatch.setattr(infrastructure, "DynamicConfigManager", lambda **_: dynamic)
    build_redis = Mock(side_effect=AssertionError("Redis built before capacity validation"))
    monkeypatch.setattr(infrastructure, "build_redis_client", build_redis)

    with pytest.raises(RuntimeError, match="must match startup"):
        await init_infrastructure_runtime(SimpleNamespace(state=SimpleNamespace()))

    managers["prisma_manager"].disconnect.assert_awaited_once()
    managers["foreground_prisma_manager"].connect.assert_not_awaited()
    dynamic.close.assert_awaited_once()
    build_redis.assert_not_called()
