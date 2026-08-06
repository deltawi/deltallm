from __future__ import annotations

import pytest


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
