"""Minimal role apps do not initialize inference or legacy service graphs."""

import json
import subprocess
import sys

import httpx
import pytest

from src.accounting_settings import AccountingProtocolSettings
from src.bootstrap.accounting_worker_app import create_accounting_worker_app
from src.config import DatabaseConnectionSettings
from src.ingress import IngressClass, ingress_class
from src.lifecycle_settings import LifecycleSettings
from src.process_lifecycle import ProcessLifecycle


def arguments():
    return dict(
        config=AccountingProtocolSettings(accounting_protocol_enabled=True),
        database=DatabaseConnectionSettings(
            url="postgresql://invalid/test", pool_size=2, pool_timeout=1
        ),
        lifecycle=ProcessLifecycle(LifecycleSettings()),
        role="request",
        owner_id="test",
        signing_secret="test-secret",
    )


def test_worker_module_does_not_import_main_provider_adapters_or_initialize_owned_clients():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys,json; import src.bootstrap.accounting_worker_app; "
            "print(json.dumps(sorted(name for name in sys.modules if name == 'src.main' or name.startswith('src.providers.') or name == 'src.bootstrap.infrastructure')))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == []


async def test_health_is_unready_before_start_and_no_provider_or_admin_routes_are_registered():
    app = create_accounting_worker_app(**arguments())
    runtime = app.state.ingress_runtime
    assert runtime.limits.enabled and runtime.limits.control_max_active == 16
    assert runtime.limits.max_body_bytes == 1_048_576
    assert ingress_class("/internal/accounting/v1/health", "GET") is IngressClass.HEALTH
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/health/readiness")).status_code == 503
        assert (await client.get("/health/liveliness")).status_code == 200
        for path in ("/openapi.json", "/docs", "/admin", "/v1/chat/completions"):
            assert (await client.get(path)).status_code == 404
        response = await client.get("/metrics")
        assert response.status_code == 503
        assert response.text == "metrics snapshot service unavailable\n"
    assert app.state.accounting_role_state.runtime is None


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "other"},
        {"signing_secret": None},
        {"signing_secret": ""},
        {"startup_seconds": True},
        {"startup_seconds": float("nan")},
        {"startup_seconds": 0},
        {"config": AccountingProtocolSettings()},
    ],
)
def test_invalid_bootstrap_input_fails_before_opening_a_pool(changes):
    with pytest.raises(ValueError):
        create_accounting_worker_app(**{**arguments(), **changes})
