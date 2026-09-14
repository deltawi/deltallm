from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.telemetry.lifecycle import WorkerHealth, WorkerState


@pytest.fixture(autouse=True)
def ready_databases(test_app):
    for name in ("prisma_manager", "foreground_prisma_manager", "telemetry_worker_prisma_manager"):
        setattr(
            test_app.state,
            name,
            SimpleNamespace(
                client=SimpleNamespace(query_raw=AsyncMock(return_value=[{"value": 1}]))
            ),
        )


@pytest.mark.parametrize("allocation", ["foreground", "telemetry_worker"])
@pytest.mark.parametrize("failure", ["missing_manager", "missing_client", "unavailable", "timeout"])
async def test_readiness_requires_each_new_database_allocation(
    client, test_app, allocation, failure
):
    if allocation == "telemetry_worker":
        test_app.state.audit_ingestion_mode = "outbox"
        test_app.state.telemetry_prisma_manager = SimpleNamespace(
            client=SimpleNamespace(query_raw=AsyncMock(return_value=[{"value": 1}]))
        )
    manager_name = allocation + "_prisma_manager"
    manager = getattr(test_app.state, manager_name)
    if failure == "missing_manager":
        delattr(test_app.state, manager_name)
    elif failure == "missing_client":
        manager.client = None
    elif failure == "unavailable":
        manager.client.query_raw.side_effect = RuntimeError("private database endpoint")
    else:

        async def blocked(*_):
            await asyncio.Event().wait()

        manager.client.query_raw.side_effect = blocked

    async with asyncio.timeout(2):
        response = await client.get("/health/readiness")
    assert response.status_code == 503
    check = allocation + "_database"
    assert response.json()["checks"][check] is False
    assert response.json()["details"][check] == {
        "state": "timeout" if failure == "timeout" else "unavailable"
    }
    assert "private" not in response.text
    assert (await client.get("/health/liveliness")).status_code == 200

    recovered = SimpleNamespace(query_raw=AsyncMock(return_value=[{"value": 1}]))
    setattr(test_app.state, manager_name, SimpleNamespace(client=recovered))
    response = await client.get("/health/readiness")
    assert response.status_code == 200
    assert response.json()["checks"][check] is True


async def test_readiness_probes_all_dependency_allocations_concurrently(client, test_app):
    entered = set()
    all_entered = asyncio.Event()

    def probe(name):
        async def run(*_):
            entered.add(name)
            if len(entered) == 5:
                all_entered.set()
            await all_entered.wait()
            return True

        return AsyncMock(side_effect=run)

    test_app.state.redis.ping = probe("redis")
    test_app.state.audit_ingestion_mode = "outbox"
    for name in (
        "prisma_manager",
        "foreground_prisma_manager",
        "telemetry_prisma_manager",
        "telemetry_worker_prisma_manager",
    ):
        setattr(
            test_app.state, name, SimpleNamespace(client=SimpleNamespace(query_raw=probe(name)))
        )
    async with asyncio.timeout(2):
        response = await client.get("/health/readiness")
    assert response.status_code == 200
    assert all_entered.is_set()
    for name in entered - {"redis"}:
        getattr(test_app.state, name).client.query_raw.assert_awaited_once_with("SELECT 1")
    test_app.state.redis.ping.assert_awaited_once_with()


async def test_legacy_readiness_does_not_probe_disabled_telemetry_allocations(client, test_app):
    worker = test_app.state.telemetry_worker_prisma_manager.client
    response = await client.get("/health/readiness")
    assert response.status_code == 200
    assert "telemetry_database" not in response.json()["checks"]
    assert "telemetry_worker_database" not in response.json()["checks"]
    worker.query_raw.assert_not_awaited()


async def test_cancelling_readiness_cancels_all_owned_probes(client, test_app):
    entered, cancelled = set(), set()
    started = asyncio.Event()

    def probe(name):
        async def run(*_):
            entered.add(name)
            if len(entered) == 3:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(name)

        return AsyncMock(side_effect=run)

    test_app.state.redis.ping = probe("redis")
    test_app.state.prisma_manager.client.query_raw = probe("control")
    test_app.state.foreground_prisma_manager.client.query_raw = probe("foreground")
    request = asyncio.create_task(client.get("/health/readiness"))
    try:
        async with asyncio.timeout(1):
            await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert entered == cancelled == {"redis", "control", "foreground"}
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("manager_name", ["redis", "prisma_manager"])
async def test_readiness_cannot_report_a_missing_required_dependency_as_ready(
    client, test_app, manager_name
):
    delattr(test_app.state, manager_name)
    response = await client.get("/health/readiness")
    assert response.status_code == 503
    name = "redis" if manager_name == "redis" else "database"
    assert response.json()["details"][name] == {"state": "unavailable"}


@pytest.mark.asyncio
async def test_health_endpoints(client):
    liveness = await client.get("/health/liveliness")
    readiness = await client.get("/health/readiness")
    health = await client.get("/health")

    assert liveness.status_code == 200
    assert liveness.json()["status"] == "ok"
    assert readiness.status_code == 200
    assert readiness.json()["status"] in {"ok", "degraded"}
    assert health.status_code == 200
    payload = health.json()
    assert payload["liveliness"] == "ok"
    assert payload["readiness"]["status"] in {"ok", "degraded"}


@pytest.mark.asyncio
async def test_readiness_tracks_expected_batch_webhook_worker(client, test_app) -> None:
    class _Task:
        def __init__(self, done: bool) -> None:
            self._done = done

        def done(self) -> bool:
            return self._done

    test_app.state.batch_webhook_worker_expected = True
    test_app.state.batch_webhook_outbox_task = _Task(done=True)

    stopped = await client.get("/health/readiness")

    assert stopped.status_code == 503
    assert stopped.json()["checks"]["batch_webhook_worker"] is False

    test_app.state.batch_webhook_outbox_task = _Task(done=False)
    running = await client.get("/health/readiness")

    assert running.status_code == 200
    assert running.json()["checks"]["batch_webhook_worker"] is True


@pytest.mark.asyncio
async def test_readiness_fails_for_expected_telemetry_worker_crash(client, test_app) -> None:
    class _Service:
        worker_health = WorkerHealth(WorkerState.FAILED, "database worker stopped")

    test_app.state.spend_tracking_service = _Service()

    response = await client.get("/health/readiness")

    assert response.status_code == 503
    payload = response.json()
    assert payload["checks"]["spend_ingestion_worker"] is False
    assert payload["details"]["spend_ingestion_worker"] == {
        "state": "failed",
        "detail": "database worker stopped",
    }


@pytest.mark.asyncio
async def test_readiness_fails_for_email_outbox_worker_failure(client, test_app) -> None:
    class _Worker:
        worker_health = WorkerHealth(
            WorkerState.FAILED,
            "required delivery audits are blocked",
        )

    test_app.state.email_outbox_worker = _Worker()

    response = await client.get("/health/readiness")

    assert response.status_code == 503
    payload = response.json()
    assert payload["checks"]["email_outbox_worker"] is False
    assert payload["details"]["email_outbox_worker"] == {
        "state": "failed",
        "detail": "required delivery audits are blocked",
    }


@pytest.mark.asyncio
async def test_policy_listener_degradation_is_visible_but_not_readiness_fatal(
    client, test_app
) -> None:
    class _Service:
        worker_health = WorkerHealth(WorkerState.READY)
        policy_listener_health = WorkerHealth(WorkerState.DEGRADED, "redis disconnected")

    test_app.state.audit_service = _Service()

    response = await client.get("/health/readiness")

    assert response.status_code == 200
    assert response.json()["details"]["audit_policy_listener"] == {
        "state": "degraded",
        "detail": "redis disconnected",
    }


@pytest.mark.asyncio
async def test_readiness_checks_dedicated_telemetry_database(client, test_app) -> None:
    class _TelemetryDB:
        def __init__(self) -> None:
            self.available = False

        async def query_raw(self, _query: str) -> list[dict[str, int]]:
            if not self.available:
                raise RuntimeError("telemetry pool unavailable")
            return [{"value": 1}]

    telemetry_db = _TelemetryDB()
    test_app.state.spend_ingestion_mode = "outbox"
    test_app.state.audit_ingestion_mode = "legacy"
    test_app.state.telemetry_prisma_manager = type(
        "TelemetryManager", (), {"client": telemetry_db}
    )()

    unavailable = await client.get("/health/readiness")
    assert unavailable.status_code == 503
    assert unavailable.json()["checks"]["telemetry_database"] is False
    assert unavailable.json()["details"]["telemetry_database"] == {"state": "unavailable"}

    telemetry_db.available = True
    recovered = await client.get("/health/readiness")
    assert recovered.status_code == 200
    assert recovered.json()["checks"]["telemetry_database"] is True


@pytest.mark.asyncio
async def test_readiness_tracks_organization_lifecycle_tasks(client, test_app) -> None:
    class _Task:
        def __init__(self, done: bool) -> None:
            self._done = done

        def done(self) -> bool:
            return self._done

    class _RuntimeHealth:
        def __init__(self, ready: bool) -> None:
            self.ready = ready

        def is_ready(self) -> bool:
            return self.ready

    test_app.state.organization_lifecycle_refresher_expected = True
    test_app.state.organization_lifecycle_task = _Task(done=False)
    test_app.state.organization_lifecycle_authorizer = _RuntimeHealth(ready=True)
    test_app.state.organization_deletion_worker_expected = True
    test_app.state.organization_deletion_task = _Task(done=True)
    test_app.state.organization_deletion_worker = _RuntimeHealth(ready=True)

    stopped = await client.get("/health/readiness")

    assert stopped.status_code == 503
    assert stopped.json()["checks"]["organization_lifecycle_refresher"] is True
    assert stopped.json()["checks"]["organization_deletion_worker"] is False

    test_app.state.organization_deletion_task = _Task(done=False)
    running = await client.get("/health/readiness")

    assert running.status_code == 200
    assert running.json()["checks"]["organization_deletion_worker"] is True

    test_app.state.organization_lifecycle_authorizer.ready = False
    stale = await client.get("/health/readiness")

    assert stale.status_code == 503
    assert stale.json()["checks"]["organization_lifecycle_refresher"] is False
