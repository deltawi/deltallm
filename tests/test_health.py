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
