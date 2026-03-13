from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_list_models_returns_runtime_models(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/models", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] >= 1
    model_names = {item["model_name"] for item in payload["data"]}
    assert "gpt-4o-mini" in model_names


@pytest.mark.asyncio
async def test_get_model_returns_health_block(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/models/gpt-4o-mini-0", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["deployment_id"] == "gpt-4o-mini-0"
    assert payload["health"]["healthy"] is True


@pytest.mark.asyncio
async def test_model_health_check_uses_runtime_checker_when_available(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def _check_once(deployment):  # noqa: ANN001, ANN202
        return SimpleNamespace(healthy=True, error=None, status_code=200, checked_at=123)

    test_app.state.background_health_checker = SimpleNamespace(check_deployment_once=_check_once)

    response = await client.post("/ui/api/models/gpt-4o-mini-0/health-check", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["deployment_id"] == "gpt-4o-mini-0"
    assert payload["status_code"] == 200
    assert payload["checked_at"] == 123
