"""Native role selection stays free of inference and legacy database engines."""

import json
import subprocess
import sys

import pytest

from src.bootstrap.server_application import accounting_owner_id, create_server_application
from src.config import AppConfig, GeneralSettings, Settings
from src.lifecycle_settings import LifecycleSettings
from src.process_lifecycle import ProcessLifecycle
from src.startup_config import StartupConfig
from tests.test_accounting_native_config import native


def startup(role, **changes):
    general = GeneralSettings(
        **native(
            accounting_projection_worker_enabled=role == "accountingWorker", **changes
        ).model_dump(),
        deployment_capacity_role=role,
    )
    return StartupConfig(
        settings=Settings(database_url="postgresql://fixture:fixture@fixture/db"),
        app_config=AppConfig(general_settings=general),
        file_config={},
        lifecycle=LifecycleSettings(),
    )


@pytest.mark.parametrize("role", ["accountingRequest", "accountingWorker"])
def test_launcher_selects_the_minimal_app_before_startup(role):
    config = startup(role)
    app = create_server_application(startup=config, lifecycle=ProcessLifecycle(config.lifecycle))
    assert app.state.accounting_role_state.runtime is None
    assert not any(route.path.startswith("/v1/") for route in app.routes)
    assert not hasattr(app.state, "prisma_manager")
    assert not hasattr(app.state, "redis")
    assert not hasattr(app.state, "http_client")


@pytest.mark.parametrize("role", ["accountingRequest", "accountingWorker"])
def test_actual_launcher_graph_has_no_main_provider_or_infrastructure_imports(role):
    script = (
        "import json,sys; from tests.test_native_server_application import startup; "
        "from src.bootstrap.server_application import create_server_application; "
        "from src.process_lifecycle import ProcessLifecycle; "
        f"config=startup('{role}'); "
        "app=create_server_application(startup=config,lifecycle=ProcessLifecycle(config.lifecycle)); "
        "print(json.dumps(sorted(name for name in sys.modules if name == 'src.main' "
        "or name.startswith('src.providers.') or name == 'src.bootstrap.infrastructure')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True, timeout=30
    )
    assert json.loads(result.stdout) == []


def test_worker_requires_database_before_opening_any_clients(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = startup("accountingWorker")
    config = StartupConfig(
        settings=Settings(database_url=None),
        app_config=config.app_config,
        file_config={},
        lifecycle=config.lifecycle,
    )
    with pytest.raises(RuntimeError, match="explicit database URL"):
        create_server_application(startup=config, lifecycle=ProcessLifecycle(config.lifecycle))


def test_local_owner_identity_changes_across_restarts():
    assert accounting_owner_id() != accounting_owner_id()
    assert len(accounting_owner_id()) <= 219
