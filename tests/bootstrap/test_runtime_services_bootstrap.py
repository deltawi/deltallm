from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.bootstrap.runtime_services import init_runtime_services, shutdown_runtime_services


def _runtime_config() -> SimpleNamespace:
    return SimpleNamespace(
        general_settings=SimpleNamespace(
            budget_alert_ttl_seconds=3600,
        ),
        deltallm_settings=SimpleNamespace(
            guardrails=[{"guardrail_name": "pii"}],
            success_callback=["success"],
            failure_callback=["failure"],
            callbacks=["shared"],
            callback_settings={"shared": {"url": "https://example.com"}},
            turn_off_message_logging=True,
        )
    )


@pytest.mark.asyncio
async def test_init_and_shutdown_runtime_services(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeGuardrailRegistry:
        def __init__(self) -> None:
            self.loaded = None

        def load_from_config(self, config) -> None:  # noqa: ANN001
            self.loaded = config

    class FakeCallbackManager:
        def __init__(self) -> None:
            self.loaded = None
            self.shutdown_called = False
            created["callback_manager"] = self

        def load_from_settings(self, **kwargs) -> None:  # noqa: ANN003
            self.loaded = kwargs

        async def shutdown(self) -> None:
            self.shutdown_called = True

    class FakeGovernanceInvalidationService:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.started = False
            self.closed = False
            created["governance_invalidation_service"] = self

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("src.bootstrap.runtime_services.PromptRegistryService", lambda **kwargs: ("prompt-registry", kwargs))
    monkeypatch.setattr("src.bootstrap.runtime_services.MCPRegistryService", lambda **kwargs: ("mcp-registry", kwargs))
    class FakeMCPGovernanceService:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.reloaded = False

        async def reload(self) -> None:
            self.reloaded = True

    monkeypatch.setattr("src.bootstrap.runtime_services.MCPGovernanceService", FakeMCPGovernanceService)
    monkeypatch.setattr("src.bootstrap.runtime_services.StreamableHTTPMCPClient", lambda client: ("mcp-client", client))
    monkeypatch.setattr("src.bootstrap.runtime_services.MCPHealthProbe", lambda **kwargs: ("mcp-health", kwargs))
    monkeypatch.setattr("src.bootstrap.runtime_services.MCPToolPolicyEnforcer", lambda limit_counter: ("policy", limit_counter))
    monkeypatch.setattr("src.bootstrap.runtime_services.MCPToolResultCache", lambda cache_backend: ("result-cache", cache_backend))
    monkeypatch.setattr("src.bootstrap.runtime_services.MCPApprovalService", lambda repository: ("approval", repository))
    monkeypatch.setattr("src.bootstrap.runtime_services.MCPGatewayService", lambda **kwargs: ("mcp-gateway", kwargs))
    monkeypatch.setattr("src.bootstrap.runtime_services.GovernanceInvalidationService", FakeGovernanceInvalidationService)
    monkeypatch.setattr("src.bootstrap.runtime_services.GuardrailRegistry", FakeGuardrailRegistry)
    monkeypatch.setattr("src.bootstrap.runtime_services.GuardrailMiddleware", lambda **kwargs: ("guardrail-middleware", kwargs))
    monkeypatch.setattr("src.bootstrap.runtime_services.CallbackManager", FakeCallbackManager)
    monkeypatch.setattr("src.bootstrap.runtime_services.AlertService", lambda **kwargs: ("alert-service", kwargs))
    monkeypatch.setattr("src.bootstrap.runtime_services.SpendLedgerService", lambda client: ("ledger", client))
    monkeypatch.setattr("src.bootstrap.runtime_services.SpendTrackingService", lambda **kwargs: ("tracking", kwargs))
    monkeypatch.setattr("src.bootstrap.runtime_services.BudgetEnforcementService", lambda **kwargs: ("budget", kwargs))

    app = SimpleNamespace(
        state=SimpleNamespace(
            prompt_registry_repository="prompt-repo",
            route_group_repository="route-group-repo",
            mcp_repository="mcp-repo",
            mcp_scope_policy_repository="mcp-scope-policy-repo",
            redis="redis-client",
            http_client="http-client",
            limit_counter="limit-counter",
            cache_backend="cache-backend",
            prisma_manager=SimpleNamespace(client="db-client"),
        )
    )

    runtime = await init_runtime_services(app, _runtime_config())

    assert app.state.prompt_registry_service[0] == "prompt-registry"
    assert app.state.mcp_gateway_service[0] == "mcp-gateway"
    assert app.state.mcp_governance_service.reloaded is True
    assert app.state.governance_invalidation_service.started is True
    assert app.state.guardrail_registry.loaded == [{"guardrail_name": "pii"}]
    assert app.state.guardrail_middleware[0] == "guardrail-middleware"
    assert app.state.turn_off_message_logging is True
    assert app.state.alert_service[0] == "alert-service"
    assert app.state.spend_ledger_service == ("ledger", "db-client")
    assert app.state.budget_service[0] == "budget"

    await shutdown_runtime_services(runtime)

    assert created["governance_invalidation_service"].closed is True
    assert created["callback_manager"].shutdown_called is True
