from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_provider_presets_endpoint_returns_known_presets(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/provider-presets", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    payload = response.json()
    items = payload["data"]
    providers = {item["provider"] for item in items}
    assert "openai" in providers
    assert "openrouter" in providers
    assert "anthropic" in providers


@pytest.mark.asyncio
async def test_create_model_rejects_unsupported_provider_mode_combo(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "bad-embed-model",
            "deltallm_params": {
                "provider": "anthropic",
                "model": "anthropic/claude-sonnet-4-20250514",
                "api_base": "https://api.anthropic.com/v1",
                "api_key": "provider-key",
            },
            "model_info": {"mode": "embedding"},
        },
    )
    assert response.status_code == 400
    assert "does not support mode" in response.text


@pytest.mark.asyncio
async def test_create_model_rejects_duplicate_model_name(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "gpt-4o-mini",
            "deltallm_params": {
                "provider": "azure",
                "model": "azure/gpt-4o-mini",
                "api_base": "https://example.azure.com/openai/v1",
                "api_key": "provider-key",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 409
    assert "Duplicate model_name 'gpt-4o-mini' is not allowed" in response.text
