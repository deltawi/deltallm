from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.bootstrap.routing import init_routing_runtime, shutdown_routing_runtime


def _routing_config(*, bootstrap_models: bool, health_checks: bool) -> SimpleNamespace:
    return SimpleNamespace(
        general_settings=SimpleNamespace(
            model_deployment_bootstrap_from_config=bootstrap_models,
            model_deployment_source="db_only",
            background_health_checks=health_checks,
            health_check_interval=60,
        ),
        router_settings=SimpleNamespace(
            routing_strategy="simple-shuffle",
            num_retries=2,
            retry_after=1.5,
            timeout=30.0,
            cooldown_time=45,
            allowed_fails=3,
            enable_pre_call_checks=False,
            model_group_alias={},
        ),
        deltallm_settings=SimpleNamespace(
            fallbacks=[],
            context_window_fallbacks=[],
            content_policy_fallbacks=[],
        ),
    )


def _base_app_state() -> SimpleNamespace:
    return SimpleNamespace(
        model_deployment_repository=object(),
        route_group_repository=object(),
        route_group_runtime_cache=object(),
        http_client=object(),
        settings=SimpleNamespace(openai_base_url="https://api.openai.com/v1"),
    )


@pytest.mark.asyncio
async def test_init_routing_runtime_wires_router_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    async def _bootstrap(repo, cfg):  # noqa: ANN001, ANN202
        calls["bootstrap"] = (repo, cfg)
        return True

    async def _load_registry(repo, cfg, settings, source_mode):  # noqa: ANN001, ANN202
        calls["load_registry"] = (repo, cfg, settings, source_mode)
        return (
            {
                "gpt-4o-mini": [
                    {
                        "deployment_id": "dep-1",
                        "deltallm_params": {"model": "openai/gpt-4o-mini"},
                        "model_info": {},
                    }
                ]
            },
            "db",
        )

    async def _load_groups(repo, cfg, route_group_cache):  # noqa: ANN001, ANN202
        calls["load_groups"] = (repo, cfg, route_group_cache)
        return (
            [
                {
                    "key": "support",
                    "enabled": True,
                    "members": [{"deployment_id": "dep-1"}],
                }
            ],
            "db",
        )

    def _configure_cache_runtime(app, app_config, redis_client, salt_key):  # noqa: ANN001
        calls["cache"] = (app, app_config, redis_client, salt_key)

    monkeypatch.setattr("src.bootstrap.routing.bootstrap_model_deployments_from_config", _bootstrap)
    monkeypatch.setattr("src.bootstrap.routing.load_model_registry", _load_registry)
    monkeypatch.setattr("src.bootstrap.routing.load_route_groups", _load_groups)
    monkeypatch.setattr("src.bootstrap.routing.configure_cache_runtime", _configure_cache_runtime)
    monkeypatch.setattr(
        "src.bootstrap.routing.ModelHotReloadManager",
        lambda **kwargs: {"dynamic_config": kwargs["dynamic_config"]},
    )

    app = SimpleNamespace(state=_base_app_state())
    cfg = _routing_config(bootstrap_models=True, health_checks=False)
    dynamic_config_manager = object()

    runtime = await init_routing_runtime(
        app,
        cfg=cfg,
        settings=app.state.settings,
        dynamic_config_manager=dynamic_config_manager,
        redis_client="redis-client",
        salt_key="salt",
    )

    assert "bootstrap" in calls
    assert app.state.model_registry["gpt-4o-mini"][0]["deployment_id"] == "dep-1"
    assert app.state.route_groups[0]["key"] == "support"
    assert app.state.router is not None
    assert app.state.router_state_backend is not None
    assert app.state.failover_manager is not None
    assert app.state.router_health_handler is not None
    assert app.state.background_health_checker is not None
    assert app.state.model_hot_reload_manager == {"dynamic_config": dynamic_config_manager}
    assert calls["cache"] == (app, cfg, "redis-client", "salt")
    assert runtime.health_task is None


@pytest.mark.asyncio
async def test_shutdown_routing_runtime_cancels_health_task(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    async def _load_registry(repo, cfg, settings, source_mode):  # noqa: ANN001, ANN202
        return (
            {
                "gpt-4o-mini": [
                    {
                        "deployment_id": "dep-1",
                        "deltallm_params": {"model": "openai/gpt-4o-mini"},
                        "model_info": {},
                    }
                ]
            },
            "db",
        )

    async def _load_groups(repo, cfg, route_group_cache):  # noqa: ANN001, ANN202
        return ([], "db")

    class FakeBackgroundHealthChecker:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.stopped = False
            created["checker"] = self

        async def start(self) -> None:
            await asyncio.sleep(3600)

        def stop(self) -> None:
            self.stopped = True

    async def _bootstrap(repo, cfg):  # noqa: ANN001, ANN202
        return False

    monkeypatch.setattr("src.bootstrap.routing.bootstrap_model_deployments_from_config", _bootstrap)
    monkeypatch.setattr("src.bootstrap.routing.load_model_registry", _load_registry)
    monkeypatch.setattr("src.bootstrap.routing.load_route_groups", _load_groups)
    monkeypatch.setattr("src.bootstrap.routing.configure_cache_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.bootstrap.routing.ModelHotReloadManager", lambda **kwargs: object())
    monkeypatch.setattr("src.bootstrap.routing.BackgroundHealthChecker", FakeBackgroundHealthChecker)

    app = SimpleNamespace(state=_base_app_state())
    cfg = _routing_config(bootstrap_models=False, health_checks=True)

    runtime = await init_routing_runtime(
        app,
        cfg=cfg,
        settings=app.state.settings,
        dynamic_config_manager=object(),
        redis_client=None,
        salt_key="salt",
    )

    assert runtime.health_task is not None

    await shutdown_routing_runtime(runtime)

    assert created["checker"].stopped is True
    assert runtime.health_task.cancelled() is True
