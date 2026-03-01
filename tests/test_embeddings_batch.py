from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_batches_endpoint_returns_404_when_disabled(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.get("/v1/batches", headers=headers)
    assert response.status_code == 404
    assert "disabled" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_files_endpoint_returns_404_when_disabled(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post(
        "/v1/files",
        headers=headers,
        files={"file": ("batch.jsonl", b"", "application/json")},
        data={"purpose": "batch"},
    )
    assert response.status_code == 404
    assert "disabled" in response.json()["detail"].lower()

