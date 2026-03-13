from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.bootstrap import BootstrapStatus
from src.main import lifespan


@pytest.mark.asyncio
async def test_lifespan_initializes_and_shuts_down_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    async def _init_infrastructure(app):  # noqa: ANN001, ANN202
        app.state.app_config = "cfg"
        app.state.settings = "settings"
        app.state.dynamic_config_manager = "dynamic-config"
        app.state.redis = "redis"
        app.state.salt_key = "salt"
        app.state.batch_repository = "batch-repo"
        calls.append("init_infrastructure")
        return SimpleNamespace(
            statuses=(BootstrapStatus("config", "ready"),),
            marker="infrastructure-runtime",
        )

    async def _shutdown_infrastructure(runtime):  # noqa: ANN001, ANN202
        calls.append(f"shutdown_infrastructure:{runtime.marker}")

    async def _init_audit(app, cfg):  # noqa: ANN001, ANN202
        calls.append(f"init_audit:{cfg}")
        return SimpleNamespace(
            statuses=(BootstrapStatus("audit", "ready"),),
            marker="audit-runtime",
        )

    async def _shutdown_audit(app, runtime):  # noqa: ANN001, ANN202
        calls.append(f"shutdown_audit:{runtime.marker}")

    async def _init_auth(app, cfg):  # noqa: ANN001, ANN202
        calls.append(f"init_auth:{cfg}")
        return SimpleNamespace(statuses=(BootstrapStatus("auth", "ready"),))

    async def _init_routing(app, **kwargs):  # noqa: ANN001, ANN202
        calls.append(f"init_routing:{kwargs['cfg']}")
        return SimpleNamespace(
            statuses=(BootstrapStatus("routing", "ready"),),
            marker="routing-runtime",
        )

    async def _shutdown_routing(runtime):  # noqa: ANN001, ANN202
        calls.append(f"shutdown_routing:{runtime.marker}")

    async def _init_runtime_services(app, cfg):  # noqa: ANN001, ANN202
        calls.append(f"init_runtime_services:{cfg}")
        return SimpleNamespace(
            statuses=(BootstrapStatus("runtime_services", "ready"),),
            marker="runtime-services",
        )

    async def _shutdown_runtime_services(runtime):  # noqa: ANN001, ANN202
        calls.append(f"shutdown_runtime_services:{runtime.marker}")

    async def _init_batch(app, cfg, repository):  # noqa: ANN001, ANN202
        calls.append(f"init_batch:{repository}")
        return SimpleNamespace(
            statuses=(BootstrapStatus("batch", "ready"),),
            marker="batch-runtime",
        )

    async def _shutdown_batch(runtime):  # noqa: ANN001, ANN202
        calls.append(f"shutdown_batch:{runtime.marker}")

    monkeypatch.setattr("src.main.init_infrastructure_runtime", _init_infrastructure)
    monkeypatch.setattr("src.main.shutdown_infrastructure_runtime", _shutdown_infrastructure)
    monkeypatch.setattr("src.main.init_audit_runtime", _init_audit)
    monkeypatch.setattr("src.main.shutdown_audit_runtime", _shutdown_audit)
    monkeypatch.setattr("src.main.init_auth_runtime", _init_auth)
    monkeypatch.setattr("src.main.init_routing_runtime", _init_routing)
    monkeypatch.setattr("src.main.shutdown_routing_runtime", _shutdown_routing)
    monkeypatch.setattr("src.main.init_runtime_services", _init_runtime_services)
    monkeypatch.setattr("src.main.shutdown_runtime_services", _shutdown_runtime_services)
    monkeypatch.setattr("src.main.init_batch_runtime", _init_batch)
    monkeypatch.setattr("src.main.shutdown_batch_runtime", _shutdown_batch)

    app = SimpleNamespace(state=SimpleNamespace())

    async with lifespan(app):
        calls.append("yield")

    assert "startup: config=ready, audit=ready, auth=ready, routing=ready, runtime_services=ready, batch=ready" in caplog.text
    assert calls == [
        "init_infrastructure",
        "init_audit:cfg",
        "init_auth:cfg",
        "init_routing:cfg",
        "init_runtime_services:cfg",
        "init_batch:batch-repo",
        "yield",
        "shutdown_batch:batch-runtime",
        "shutdown_runtime_services:runtime-services",
        "shutdown_routing:routing-runtime",
        "shutdown_audit:audit-runtime",
        "shutdown_infrastructure:infrastructure-runtime",
    ]


@pytest.mark.asyncio
async def test_lifespan_cleans_up_partial_startup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _init_infrastructure(app):  # noqa: ANN001, ANN202
        app.state.app_config = "cfg"
        app.state.settings = "settings"
        app.state.dynamic_config_manager = "dynamic-config"
        app.state.redis = "redis"
        app.state.salt_key = "salt"
        app.state.batch_repository = "batch-repo"
        calls.append("init_infrastructure")
        return SimpleNamespace(
            statuses=(BootstrapStatus("config", "ready"),),
            marker="infrastructure-runtime",
        )

    async def _shutdown_infrastructure(runtime):  # noqa: ANN001, ANN202
        calls.append(f"shutdown_infrastructure:{runtime.marker}")

    async def _init_audit(app, cfg):  # noqa: ANN001, ANN202
        calls.append(f"init_audit:{cfg}")
        return SimpleNamespace(
            statuses=(BootstrapStatus("audit", "ready"),),
            marker="audit-runtime",
        )

    async def _shutdown_audit(app, runtime):  # noqa: ANN001, ANN202
        calls.append(f"shutdown_audit:{runtime.marker}")

    async def _init_auth(app, cfg):  # noqa: ANN001, ANN202
        calls.append(f"init_auth:{cfg}")
        return SimpleNamespace(statuses=(BootstrapStatus("auth", "ready"),))

    async def _init_routing(app, **kwargs):  # noqa: ANN001, ANN202
        calls.append(f"init_routing:{kwargs['cfg']}")
        raise RuntimeError("routing failed")

    monkeypatch.setattr("src.main.init_infrastructure_runtime", _init_infrastructure)
    monkeypatch.setattr("src.main.shutdown_infrastructure_runtime", _shutdown_infrastructure)
    monkeypatch.setattr("src.main.init_audit_runtime", _init_audit)
    monkeypatch.setattr("src.main.shutdown_audit_runtime", _shutdown_audit)
    monkeypatch.setattr("src.main.init_auth_runtime", _init_auth)
    monkeypatch.setattr("src.main.init_routing_runtime", _init_routing)

    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="routing failed"):
        async with lifespan(app):
            pass

    assert calls == [
        "init_infrastructure",
        "init_audit:cfg",
        "init_auth:cfg",
        "init_routing:cfg",
        "shutdown_audit:audit-runtime",
        "shutdown_infrastructure:infrastructure-runtime",
    ]
