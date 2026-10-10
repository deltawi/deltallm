from types import SimpleNamespace

from src.concurrency import BoundedCapacityGate
from src.services.identity.external.external_auth_runtime import ExternalAuthRuntime


async def test_operational_diagnostics_are_private_and_work_when_disabled(client, test_app):
    test_app.state.settings.master_key = "mk-test"
    assert (await client.get("/ui/api/external-auth/status")).status_code == 401
    response = await client.get("/ui/api/external-auth/status", headers={"X-Master-Key": "mk-test"})
    assert response.status_code == 200
    assert response.json()["state"] == "disabled"
    assert response.json()["protocol"] == "external_customer_v1"
    assert "integrations" not in response.text and "public_key" not in response.text
    mixed = await client.get(
        "/ui/api/external-auth/status",
        headers={"X-Master-Key": "mk-test"},
        cookies={"deltallm_session": "psk_ext1_expired"},
    )
    assert mixed.status_code == 403


async def test_operational_diagnostics_report_degraded_dependencies(client, test_app):
    test_app.state.settings.master_key = "mk-test"
    runtime = ExternalAuthRuntime.__new__(ExternalAuthRuntime)
    runtime.cleanup_healthy = False
    runtime.crypto = SimpleNamespace(ready=True)
    runtime.cache_worker_ready = lambda: False
    runtime.transactions = SimpleNamespace(
        gates={
            "mutation": BoundedCapacityGate(concurrency=2, max_waiters=8),
            "validation": BoundedCapacityGate(concurrency=1, max_waiters=8),
        }
    )

    async def unhealthy():
        return False

    runtime.check_ready = unhealthy
    test_app.state.external_auth_runtime = runtime
    response = await client.get("/ui/api/external-auth/status", headers={"X-Master-Key": "mk-test"})
    assert response.status_code == 200
    assert response.json()["state"] == "degraded" and response.json()["cache_worker_ready"] is False
