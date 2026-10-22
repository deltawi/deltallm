from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.middleware.errors import proxy_error_response
from src.models.errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    InvalidRequestError,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
)
from tests.test_chat import _StreamContext


def _auth(test_app) -> dict[str, str]:
    return {"Authorization": f"Bearer {test_app.state._test_key}"}


def _chat(**extra: Any) -> dict[str, Any]:
    return {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], **extra}


@pytest.mark.asyncio
async def test_bad_key_uses_the_openai_error_envelope(client):
    response = await client.post(
        "/v1/chat/completions", headers={"Authorization": "Bearer sk-wrong"}, json=_chat()
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "message": "Invalid API key",
            "type": "authentication_error",
            "param": None,
            "code": None,
        }
    }


@pytest.mark.asyncio
async def test_validation_errors_are_400_and_name_the_parameter(client, test_app):
    missing = await client.post(
        "/v1/chat/completions", headers=_auth(test_app), json={"model": "gpt-4o-mini"}
    )
    out_of_range = await client.post(
        "/v1/chat/completions", headers=_auth(test_app), json=_chat(temperature=5)
    )

    assert missing.status_code == 400
    assert missing.headers["x-should-retry"] == "false"
    assert missing.json()["error"] == {
        "message": "Missing required parameter: 'messages'",
        "type": "invalid_request_error",
        "param": "messages",
        "code": None,
    }
    assert out_of_range.status_code == 400
    assert out_of_range.json()["error"]["param"] == "temperature"
    assert out_of_range.json()["error"]["message"].startswith("Invalid value for 'temperature'")


@pytest.mark.asyncio
async def test_admin_routes_keep_the_fastapi_error_shape(client):
    response = await client.get("/ui/api/keys")

    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_chat_stream_failure_after_output_sends_an_error_frame(client, test_app):
    def stream(method, url, headers, json, timeout):  # noqa: ANN001, ANN202
        del method, url, headers, json, timeout
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"c","object":"chat.completion.chunk","choices":[{"index":0,'
                '"delta":{"role":"assistant","content":"partial"},"finish_reason":null}]}',
            ],
            line_error=httpx.ReadError("connection reset"),
        )

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions", headers=_auth(test_app), json=_chat(stream=True)
    )

    assert response.status_code == 200
    frames = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert "partial" in frames[0]
    assert "[DONE]" not in response.text
    error = json.loads(frames[-1].removeprefix("data: "))["error"]
    assert set(error) >= {"message", "type", "code"}
    assert "connection reset" not in response.text


@pytest.mark.parametrize(
    ("error", "should_retry"),
    [
        (BudgetExceededError(), "false"),
        (ApprovalRequiredError(), "false"),
        (InvalidRequestError(), "false"),
        (RateLimitError(retry_after=3), None),
        (TimeoutError(), None),
        (ServiceUnavailableError(), None),
    ],
)
def test_should_retry_header_marks_errors_a_retry_cannot_fix(error, should_retry):
    response = proxy_error_response(error)

    assert response.headers.get("x-should-retry") == should_retry


@pytest.mark.asyncio
async def test_embeddings_on_a_chat_model_is_a_400(client, test_app):
    response = await client.post(
        "/v1/embeddings", headers=_auth(test_app), json={"model": "gpt-4o-mini", "input": "x"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_model_mode"
    assert response.json()["error"]["message"] == (
        "Model 'gpt-4o-mini' does not support embedding requests"
    )
