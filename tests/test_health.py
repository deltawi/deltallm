from __future__ import annotations

import pytest

from src.telemetry.lifecycle import WorkerHealth, WorkerState


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
