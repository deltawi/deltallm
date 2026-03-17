from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_team_create_rejects_legacy_models_field(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/teams",
        headers={"Authorization": "Bearer mk-test"},
        json={"team_id": "team-1", "organization_id": "org-1", "models": ["gpt-4o-mini"]},
    )

    assert response.status_code == 400
    assert "callable-target bindings" in response.text


@pytest.mark.asyncio
async def test_team_update_rejects_legacy_models_field(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.put(
        "/ui/api/teams/team-1",
        headers={"Authorization": "Bearer mk-test"},
        json={"models": ["gpt-4o-mini"]},
    )

    assert response.status_code == 400
    assert "callable-target bindings" in response.text


@pytest.mark.asyncio
async def test_key_create_rejects_legacy_models_field(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/keys",
        headers={"Authorization": "Bearer mk-test"},
        json={"key_name": "Legacy", "team_id": "team-1", "models": ["gpt-4o-mini"]},
    )

    assert response.status_code == 400
    assert "callable-target bindings" in response.text


@pytest.mark.asyncio
async def test_key_update_rejects_legacy_models_field(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.put(
        "/ui/api/keys/key-hash-1",
        headers={"Authorization": "Bearer mk-test"},
        json={"models": ["gpt-4o-mini"]},
    )

    assert response.status_code == 400
    assert "callable-target bindings" in response.text
