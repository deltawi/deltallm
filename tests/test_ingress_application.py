from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.config import GeneralSettings, Settings
from src.ingress import initialize_ingress

pytestmark = [pytest.mark.app, pytest.mark.asyncio]


async def test_ingress_wraps_platform_auth_cache_and_error_accounting(
    test_app, client, monkeypatch
) -> None:
    initialize_ingress(
        test_app,
        GeneralSettings(gateway_ingress_enabled=True, gateway_ingress_max_active=1),
        Settings(),
    )
    rt = test_app.state.ingress_runtime
    await rt.requests.acquire(timeout_seconds=1)
    platform_auth = AsyncMock()
    monkeypatch.setattr("src.main.attach_platform_auth_context", platform_auth)
    key_validation = AsyncMock()
    monkeypatch.setattr(test_app.state.key_service, "validate_key", key_validation)
    redis = AsyncMock()
    monkeypatch.setattr(test_app.state, "redis", redis)
    failures = AsyncMock()
    monkeypatch.setattr(test_app.state.spend_tracking_service, "log_request_failure", failures)
    try:
        response = await client.post("/v1/chat/completions", content=b"invalid json")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "gateway_ingress_full"
        assert response.headers["retry-after"] == "1"
        platform_auth.assert_not_called()
        key_validation.assert_not_called()
        assert not redis.mock_calls
        failures.assert_not_called()
        assert test_app.state._test_repo.calls == 0
        assert test_app.state.http_client.post_calls == 0
    finally:
        await rt.requests.release()


@pytest.mark.parametrize("stream", [False, True])
async def test_admitted_chat_preserves_auth_provider_and_body_flow(
    test_app, client, stream
) -> None:
    initialize_ingress(test_app, GeneralSettings(gateway_ingress_enabled=True), Settings())
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": stream,
        },
    )
    assert response.status_code == 200
    if stream:
        assert "[DONE]" in response.text
        assert test_app.state.http_client.stream_calls == 1
    else:
        assert response.json()["choices"][0]["message"]["content"] == "ok"
        assert test_app.state.http_client.post_calls == 1
    assert test_app.state._test_repo.calls == 1
    rt = test_app.state.ingress_runtime
    assert rt.requests.active == rt.requests.waiters == rt.buffered_bytes == 0
