from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.chat.message_token_count import estimate_input_tokens
from src.router.router import Deployment

COUNT_BODY: dict[str, Any] = {
    "model": "gpt-4o-mini",
    "system": [
        {"type": "text", "text": "You are Claude Code.", "cache_control": {"type": "ephemeral"}}
    ],
    "messages": [{"role": "user", "content": "fix the bug in main.py"}],
    "tools": [
        {
            "name": "Read",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ],
    "thinking": {"type": "enabled", "budget_tokens": 2048},
    "metadata": {"user_id": "user_abc", "prompt_ref": "deltallm-only"},
}


def _use_anthropic_deployment(test_app, *, api_key: str = "provider-key") -> None:  # noqa: ANN001
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "anthropic"
    deployment.deltallm_params["model"] = "anthropic/claude-sonnet-4-6"
    deployment.deltallm_params["api_key"] = api_key


def _anthropic_deployment(deployment_id: str, api_base: str, priority: int) -> Deployment:
    return Deployment(
        deployment_id=deployment_id,
        model_name="gpt-4o-mini",
        deltallm_params={
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
            "api_key": "provider-key",
            "api_base": api_base,
        },
        priority=priority,
    )


def _post_returning(response: httpx.Response | Exception, calls: list[dict[str, Any]]):
    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del content
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if isinstance(response, Exception):
            raise response
        return response

    return post


async def _count(client, test_app, body: dict[str, Any] = COUNT_BODY, **headers: str):  # noqa: ANN001, ANN201
    return await client.post(
        "/v1/messages/count_tokens?beta=true",
        headers={"x-api-key": test_app.state._test_key, **headers},
        json=body,
    )


@pytest.mark.asyncio
async def test_count_tokens_forwards_body_and_headers_to_anthropic(client, test_app):
    _use_anthropic_deployment(test_app)
    calls: list[dict[str, Any]] = []
    test_app.state.http_client.post = _post_returning(
        httpx.Response(200, json={"input_tokens": 1234}), calls
    )

    response = await _count(
        client,
        test_app,
        **{"anthropic-beta": "token-counting-2024-11-01", "anthropic-version": "2023-06-01"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"input_tokens": 1234}
    assert response.headers["x-deltallm-token-count"] == "provider"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.anthropic.com/v1/messages/count_tokens"
    assert calls[0]["headers"]["x-api-key"] == "provider-key"
    assert calls[0]["headers"]["anthropic-beta"] == "token-counting-2024-11-01"
    sent = calls[0]["json"]
    assert sent["model"] == "claude-sonnet-4-6"
    assert "metadata" not in sent
    assert sent["system"] == COUNT_BODY["system"]
    assert sent["messages"] == COUNT_BODY["messages"]
    assert sent["tools"] == COUNT_BODY["tools"]
    assert sent["thinking"] == COUNT_BODY["thinking"]
    assert calls[0]["timeout"].read <= 10


@pytest.mark.asyncio
async def test_count_tokens_estimates_for_non_anthropic_deployment(client, test_app):
    calls: list[dict[str, Any]] = []
    test_app.state.http_client.post = _post_returning(httpx.Response(500), calls)

    response = await _count(client, test_app)

    assert response.status_code == 200, response.text
    assert response.json() == {"input_tokens": estimate_input_tokens(COUNT_BODY)}
    assert response.headers["x-deltallm-token-count"] == "estimate"
    assert calls == []


@pytest.mark.asyncio
async def test_count_tokens_accepts_bearer_auth(client, test_app):
    response = await client.post(
        "/v1/messages/count_tokens",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json=COUNT_BODY,
    )

    assert response.status_code == 200
    assert response.json()["input_tokens"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upstream",
    [
        httpx.Response(503, json={"type": "error", "error": {"type": "overloaded_error"}}),
        httpx.Response(200, json={"input_tokens": "many"}),
        httpx.Response(200, content=b"not json"),
        httpx.ReadTimeout("slow"),
        httpx.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API.",
                },
            },
        ),
    ],
    ids=["unavailable", "malformed-count", "not-json", "timeout", "account-error"],
)
async def test_count_tokens_falls_back_to_estimate_when_provider_cannot_count(
    client, test_app, upstream
):
    _use_anthropic_deployment(test_app)
    calls: list[dict[str, Any]] = []
    test_app.state.http_client.post = _post_returning(upstream, calls)

    response = await _count(client, test_app)

    assert response.status_code == 200, response.text
    assert response.json() == {"input_tokens": estimate_input_tokens(COUNT_BODY)}
    assert response.headers["x-deltallm-token-count"] == "estimate"
    assert len(calls) == 1
    assert "credit balance" not in response.text


@pytest.mark.asyncio
async def test_count_tokens_forwards_client_error_wording(client, test_app):
    _use_anthropic_deployment(test_app)
    message = "messages.0.content.0.type: Input tag 'new_block' does not match"
    test_app.state.http_client.post = _post_returning(
        httpx.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": message}},
        ),
        [],
    )

    response = await _count(client, test_app)

    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": message},
    }


@pytest.mark.asyncio
async def test_count_tokens_tries_next_anthropic_deployment(client, test_app):
    registry = test_app.state.router.deployment_registry
    registry["gpt-4o-mini"] = [
        _anthropic_deployment("second", "https://second.example/v1", priority=1),
        _anthropic_deployment("first", "https://first.example/v1", priority=0),
    ]
    calls: list[str] = []

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del headers, json, content, timeout
        calls.append(url)
        if url.startswith("https://first.example"):
            return httpx.Response(529, json={"type": "error"})
        return httpx.Response(200, json={"input_tokens": 77})

    test_app.state.http_client.post = post

    response = await _count(client, test_app)

    assert response.json() == {"input_tokens": 77}
    assert calls == [
        "https://first.example/v1/messages/count_tokens",
        "https://second.example/v1/messages/count_tokens",
    ]


@pytest.mark.asyncio
async def test_count_tokens_skips_deployment_without_provider_key(client, test_app):
    _use_anthropic_deployment(test_app, api_key="")
    calls: list[dict[str, Any]] = []
    test_app.state.http_client.post = _post_returning(httpx.Response(200), calls)

    response = await _count(client, test_app)

    assert response.headers["x-deltallm-token-count"] == "estimate"
    assert calls == []


@pytest.mark.asyncio
async def test_count_tokens_checks_model_access(client, test_app):
    calls: list[dict[str, Any]] = []
    test_app.state.http_client.post = _post_returning(httpx.Response(200), calls)

    response = await _count(client, test_app, {**COUNT_BODY, "model": "claude-opus-4-8"})

    assert response.status_code in {403, 404}
    assert response.json()["type"] == "error"
    assert calls == []


@pytest.mark.asyncio
async def test_count_tokens_requires_auth(client) -> None:
    response = await client.post("/v1/messages/count_tokens", json=COUNT_BODY)

    assert response.status_code == 401
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"model": "gpt-4o-mini"}, "messages"),
        ({"model": "", "messages": []}, "model"),
        (
            {**COUNT_BODY, "mcp_servers": [{"type": "url", "url": "https://mcp.example"}]},
            "mcp_servers",
        ),
    ],
)
async def test_count_tokens_rejects_invalid_body(client, test_app, body, fragment):
    response = await _count(client, test_app, body)

    assert response.status_code == 400
    assert response.json()["type"] == "error"
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert fragment in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_count_tokens_malformed_json_uses_anthropic_envelope(client, test_app):
    response = await client.post(
        "/v1/messages/count_tokens",
        headers={"x-api-key": test_app.state._test_key, "content-type": "application/json"},
        content="{bad",
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Invalid JSON body"


def test_estimate_counts_text_tools_and_fixed_cost_blocks() -> None:
    text_only = {"messages": [{"role": "user", "content": "a" * 300}]}
    assert estimate_input_tokens(text_only) == 4 + 100

    with_image = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "A" * 9000},
                    }
                ],
            }
        ]
    }
    assert estimate_input_tokens(with_image) == 4 + 1600

    signed = {
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "abc", "signature": "S" * 9000}],
            }
        ]
    }
    assert estimate_input_tokens(signed) == 4 + 1

    with_tools = {**text_only, "tools": COUNT_BODY["tools"]}
    assert estimate_input_tokens(with_tools) > estimate_input_tokens(text_only)
    assert estimate_input_tokens({}) == 1
