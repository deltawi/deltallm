from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_ui_sso_url_endpoint_returns_login_path(client, test_app):
    response = await client.get("/ui/api/auth/sso-url")

    assert response.status_code == 200
    assert response.json() == {"url": "/auth/login"}
