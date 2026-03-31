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


@pytest.mark.asyncio
async def test_provider_health_summary_aggregates_provider_statuses(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": "dep-openai-1",
                "deltallm_params": {
                    "provider": "openai",
                    "model": "openai/gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "provider-key",
                },
            },
            {
                "deployment_id": "dep-openai-2",
                "deltallm_params": {
                    "provider": "openai",
                    "model": "openai/gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "provider-key",
                },
            },
        ],
        "claude-sonnet": [
            {
                "deployment_id": "dep-anthropic-1",
                "deltallm_params": {
                    "provider": "anthropic",
                    "model": "anthropic/claude-sonnet-4-20250514",
                    "api_base": "https://api.anthropic.com/v1",
                    "api_key": "provider-key",
                },
            },
        ],
        "azure-gpt": [
            {
                "deployment_id": "dep-azure-1",
                "deltallm_params": {
                    "provider": "azure_openai",
                    "model": "azure_openai/gpt-4o-mini",
                    "api_base": "https://resource.openai.azure.com/openai/v1",
                    "api_key": "provider-key",
                },
            },
        ],
    }

    await test_app.state.router_state_backend.set_health("dep-openai-2", False)
    await test_app.state.router_state_backend.set_cooldown("dep-anthropic-1", 60, "test-cooldown")

    response = await client.get("/ui/api/models/provider-health-summary", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_models"] == 4
    assert payload["summary"] == {
        "total_providers": 3,
        "active_providers": 2,
        "down_providers": 1,
    }

    providers = {item["provider"]: item for item in payload["providers"]}
    assert providers["openai"]["models"] == 2
    assert providers["openai"]["healthy_models"] == 1
    assert providers["openai"]["unhealthy_models"] == 1
    assert providers["openai"]["status"] == "degraded"
    assert providers["anthropic"]["status"] == "down"
    assert providers["azure_openai"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_provider_health_summary_counts_models_beyond_paginated_limit(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": f"dep-openai-{index}",
                "deltallm_params": {
                    "provider": "openai",
                    "model": "openai/gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "provider-key",
                },
            }
            for index in range(501)
        ]
    }

    response = await client.get("/ui/api/models/provider-health-summary", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_models"] == 501
    assert payload["summary"]["total_providers"] == 1
    assert payload["providers"][0]["provider"] == "openai"
    assert payload["providers"][0]["models"] == 501
    assert payload["providers"][0]["healthy_models"] == 501
