"""The public HTTP path shares the resolved ID with successful spend writes."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

pytestmark = pytest.mark.app


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("supplied", [None, "", "bad id", "a" * 257, "caller-123"])
async def test_chat_response_and_accounting_have_the_same_id(
    test_app, client, monkeypatch, stream, supplied
):
    log_spend = AsyncMock()
    monkeypatch.setattr(test_app.state.spend_tracking_service, "log_spend", log_spend)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    if supplied is not None:
        headers["x-request-id"] = supplied
    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": stream,
        },
    )
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    if supplied == "caller-123":
        assert request_id == supplied
    else:
        assert UUID(request_id).version == 4
    log_spend.assert_awaited_once()
    assert log_spend.call_args.kwargs["request_id"] == request_id


async def test_auth_denial_has_a_correlation_id(client):
    response = await client.post(
        "/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": []}
    )
    assert response.status_code == 401
    assert UUID(response.headers["x-request-id"]).version == 4
