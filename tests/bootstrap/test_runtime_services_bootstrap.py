from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.bootstrap.runtime_services import init_runtime_services, shutdown_runtime_services


def _runtime_config(
    *,
    tier_policy_mode: str = "shadow",
    tier_policy_missing_service_mode: str = "fail_closed",
    tier_policy_refresh_interval_seconds: float = 300.0,
    tier_policy_refresh_jitter_seconds: float = 1.0,
    tier_policy_transition_grace_seconds: float = 0.05,
    tier_policy_refresh_retry_delay_seconds: float = 5.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        general_settings=SimpleNamespace(
            budget_alert_ttl_seconds=3600,
            tier_policy_mode=tier_policy_mode,
            tier_policy_missing_service_mode=tier_policy_missing_service_mode,
            tier_policy_refresh_interval_seconds=tier_policy_refresh_interval_seconds,
            tier_policy_refresh_jitter_seconds=tier_policy_refresh_jitter_seconds,
            tier_policy_transition_grace_seconds=tier_policy_transition_grace_seconds,
            tier_policy_refresh_retry_delay_seconds=tier_policy_refresh_retry_delay_seconds,
        ),
        deltallm_settings=SimpleNamespace(
            guardrails=[{"guardrail_name": "pii"}],
            success_callback=["success"],
            failure_callback=["failure"],
            callbacks=["shared"],
            callback_settings={"shared": {"url": "https://example.com"}},
            turn_off_message_logging=True,
        ),
    )


def _runtime_config_without_tier_policy_settings() -> SimpleNamespace:
    config = _runtime_config()
    for field_name in (
        "tier_policy_mode",
        "tier_policy_missing_service_mode",
        "tier_policy_refresh_interval_seconds",
        "tier_policy_refresh_jitter_seconds",
        "tier_policy_transition_grace_seconds",
        "tier_policy_refresh_retry_delay_seconds",
    ):
        delattr(config.general_settings, field_name)
    return config


def _runtime_app(*, settings: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            prompt_registry_repository="prompt-repo",
            route_group_repository="route-group-repo",
            tier_repository="tier-repo",
            mcp_repository="mcp-repo",
            mcp_scope_policy_repository="mcp-scope-policy-repo",
            redis="redis-client",
            http_client="http-client",
            upstream_http_settings="startup-upstream-settings",
            limit_counter="limit-counter",
            cache_backend="cache-backend",
            prisma_manager=SimpleNamespace(client="db-client"),
        )
    )


def _status_map(runtime) -> dict[str, str]:  # noqa: ANN001
    return {status.name: status.state for status in runtime.statuses}


def _install_runtime_service_fakes(
    monkeypatch: pytest.MonkeyPatch,
    created: dict[str, object],
    *,
    tier_policy_reload_error: Exception | None = None,
    prompt_registry_error: Exception | None = None,
) -> None:
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

    class FakeTierPolicyService:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.mode = kwargs["mode"]
            self.missing_service_mode = kwargs["missing_service_mode"]
            self.reloaded = False
            self.started = False
            self.closed = False
            created["tier_policy_service"] = self

        async def reload(self) -> None:
            self.reloaded = True
            if tier_policy_reload_error is not None:
                raise tier_policy_reload_error

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

    class FakeMCPGovernanceService:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.reloaded = False

        async def reload(self) -> None:
            self.reloaded = True

    def fake_prompt_registry_service(**kwargs):  # noqa: ANN003, ANN202
        if prompt_registry_error is not None:
            raise prompt_registry_error
        return ("prompt-registry", kwargs)

    monkeypatch.setattr(
        "src.bootstrap.runtime_services.PromptRegistryService",
        fake_prompt_registry_service,
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.MCPRegistryService",
        lambda **kwargs: ("mcp-registry", kwargs),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.MCPGovernanceService",
        FakeMCPGovernanceService,
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.StreamableHTTPMCPClient",
        lambda client, **kwargs: ("mcp-client", client, kwargs),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.MCPHealthProbe",
        lambda **kwargs: ("mcp-health", kwargs),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.MCPToolPolicyEnforcer",
        lambda limit_counter: ("policy", limit_counter),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.MCPToolResultCache",
        lambda cache_backend: ("result-cache", cache_backend),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.MCPApprovalService",
        lambda repository: ("approval", repository),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.MCPGatewayService",
        lambda **kwargs: ("mcp-gateway", kwargs),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.GovernanceInvalidationService",
        FakeGovernanceInvalidationService,
    )
    monkeypatch.setattr("src.bootstrap.runtime_services.TierPolicyService", FakeTierPolicyService)
    monkeypatch.setattr("src.bootstrap.runtime_services.GuardrailRegistry", FakeGuardrailRegistry)
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.GuardrailMiddleware",
        lambda **kwargs: ("guardrail-middleware", kwargs),
    )
    monkeypatch.setattr("src.bootstrap.runtime_services.CallbackManager", FakeCallbackManager)
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.AlertService",
        lambda **kwargs: ("alert-service", kwargs),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.SpendLedgerService",
        lambda client: ("ledger", client),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.SpendTrackingService",
        lambda **kwargs: ("tracking", kwargs),
    )
    monkeypatch.setattr(
        "src.bootstrap.runtime_services.BudgetEnforcementService",
        lambda **kwargs: ("budget", kwargs),
    )


@pytest.mark.asyncio
async def test_init_and_shutdown_runtime_services(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}
    _install_runtime_service_fakes(monkeypatch, created)
    app = _runtime_app()

    runtime = await init_runtime_services(app, _runtime_config())

    assert app.state.prompt_registry_service[0] == "prompt-registry"
    assert app.state.mcp_transport_client == (
        "mcp-client",
        "http-client",
        {"general_settings": "startup-upstream-settings"},
    )
    assert app.state.mcp_gateway_service[0] == "mcp-gateway"
    assert app.state.mcp_governance_service.reloaded is True
    assert app.state.tier_policy_service.reloaded is True
    assert app.state.tier_policy_service.kwargs == {
        "repository": "tier-repo",
        "mode": "shadow",
        "missing_service_mode": "fail_closed",
        "refresh_interval_seconds": 300.0,
        "refresh_jitter_seconds": 1.0,
        "transition_grace_seconds": 0.05,
        "refresh_retry_delay_seconds": 5.0,
    }
    assert app.state.tier_policy_service.started is True
    assert _status_map(runtime)["tier_policy"] == "ready"
    assert app.state.governance_invalidation_service.started is True
    assert (
        created["governance_invalidation_service"].kwargs["tier_policy_service"]
        is app.state.tier_policy_service
    )
    assert app.state.guardrail_registry.loaded == [{"guardrail_name": "pii"}]
    assert app.state.guardrail_middleware[0] == "guardrail-middleware"
    assert app.state.turn_off_message_logging is True
    assert app.state.alert_service[0] == "alert-service"
    assert app.state.spend_ledger_service == ("ledger", "db-client")
    assert app.state.budget_service[0] == "budget"

    await shutdown_runtime_services(runtime)

    assert created["governance_invalidation_service"].closed is True
    assert created["tier_policy_service"].closed is True
    assert created["callback_manager"].shutdown_called is True


@pytest.mark.asyncio
async def test_init_runtime_services_skips_tier_policy_reload_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    _install_runtime_service_fakes(monkeypatch, created, tier_policy_reload_error=RuntimeError())
    app = _runtime_app()

    runtime = await init_runtime_services(app, _runtime_config(tier_policy_mode="disabled"))

    assert app.state.tier_policy_service.reloaded is False
    assert app.state.tier_policy_service.started is False
    assert _status_map(runtime)["tier_policy"] == "disabled"
    assert created["governance_invalidation_service"].kwargs["tier_policy_service"] is None

    await shutdown_runtime_services(runtime)


@pytest.mark.asyncio
async def test_init_runtime_services_degrades_tier_policy_reload_failure_when_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    _install_runtime_service_fakes(monkeypatch, created, tier_policy_reload_error=RuntimeError())
    app = _runtime_app()

    runtime = await init_runtime_services(
        app,
        _runtime_config(
            tier_policy_mode="shadow",
            tier_policy_missing_service_mode="fail_open",
        ),
    )

    assert app.state.tier_policy_service.reloaded is True
    assert app.state.tier_policy_service.started is True
    assert _status_map(runtime)["tier_policy"] == "degraded"

    await shutdown_runtime_services(runtime)


@pytest.mark.asyncio
async def test_init_runtime_services_raises_tier_policy_reload_failure_when_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    _install_runtime_service_fakes(
        monkeypatch,
        created,
        tier_policy_reload_error=RuntimeError("snapshot unavailable"),
    )

    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        await init_runtime_services(
            _runtime_app(),
            _runtime_config(
                tier_policy_mode="shadow",
                tier_policy_missing_service_mode="fail_closed",
            ),
        )


@pytest.mark.asyncio
async def test_init_runtime_services_does_not_start_tier_policy_when_later_bootstrap_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    _install_runtime_service_fakes(
        monkeypatch,
        created,
        prompt_registry_error=RuntimeError("prompt registry unavailable"),
    )

    with pytest.raises(RuntimeError, match="prompt registry unavailable"):
        await init_runtime_services(_runtime_app(), _runtime_config())

    tier_policy_service = created["tier_policy_service"]
    assert tier_policy_service.reloaded is True
    assert tier_policy_service.started is False


@pytest.mark.asyncio
async def test_init_runtime_services_uses_settings_fallback_for_tier_policy_when_config_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    _install_runtime_service_fakes(monkeypatch, created)
    app = _runtime_app(
        settings=SimpleNamespace(
            tier_policy_mode="shadow",
            tier_policy_missing_service_mode="fail_closed",
            tier_policy_refresh_interval_seconds=42.0,
            tier_policy_refresh_jitter_seconds=0.0,
            tier_policy_transition_grace_seconds=0.2,
            tier_policy_refresh_retry_delay_seconds=3.0,
        )
    )

    runtime = await init_runtime_services(app, _runtime_config_without_tier_policy_settings())

    assert app.state.tier_policy_service.kwargs == {
        "repository": "tier-repo",
        "mode": "shadow",
        "missing_service_mode": "fail_closed",
        "refresh_interval_seconds": 42.0,
        "refresh_jitter_seconds": 0.0,
        "transition_grace_seconds": 0.2,
        "refresh_retry_delay_seconds": 3.0,
    }
    assert app.state.tier_policy_service.started is True
    assert _status_map(runtime)["tier_policy"] == "ready"

    await shutdown_runtime_services(runtime)


@pytest.mark.asyncio
async def test_init_runtime_services_prefers_explicit_config_over_settings_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    _install_runtime_service_fakes(monkeypatch, created)
    app = _runtime_app(
        settings=SimpleNamespace(
            tier_policy_mode="shadow",
            tier_policy_missing_service_mode="fail_closed",
        )
    )

    runtime = await init_runtime_services(app, _runtime_config(tier_policy_mode="disabled"))

    assert app.state.tier_policy_service.kwargs["mode"] == "disabled"
    assert app.state.tier_policy_service.reloaded is False
    assert app.state.tier_policy_service.started is False
    assert _status_map(runtime)["tier_policy"] == "disabled"

    await shutdown_runtime_services(runtime)
