from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.providers.anthropic import AnthropicAdapter
from src.providers.resolution import provider_presets, provider_supports_mode


def _deployment_payload(*, model_name: str, provider: str, mode: str, upstream_model: str) -> dict[str, Any]:
    deltallm_params: dict[str, Any] = {
        "provider": provider,
        "model": upstream_model,
        "api_key": "provider-key",
    }
    if provider == "bedrock":
        deltallm_params.pop("api_key", None)
        deltallm_params["region"] = "us-east-1"
        deltallm_params["aws_access_key_id"] = "AKIDEXAMPLE"
        deltallm_params["aws_secret_access_key"] = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"

    return {
        "model_name": model_name,
        "deltallm_params": deltallm_params,
        "model_info": {"mode": mode},
    }


def _install_mock_provider_post(test_app) -> None:  # noqa: ANN001
    async def post(  # noqa: ANN001, ANN201
        url: str,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        timeout: int = 0,
        content: bytes | None = None,
        **kwargs,
    ):
        del timeout, kwargs, headers, content
        if ":generateContent?key=" in url:
            payload = {
                "responseId": "resp_test",
                "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
            }
            return httpx.Response(200, json=payload)

        if "/converse" in url:
            payload = {
                "requestId": "req_test",
                "output": {"message": {"content": [{"text": "ok"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            }
            return httpx.Response(200, json=payload)

        if url.endswith("/messages"):
            payload = {
                "id": "msg_test",
                "model": json.get("model", "claude-sonnet"),
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": "end_turn",
            }
            return httpx.Response(200, json=payload)

        if url.endswith("/chat/completions"):
            payload = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json.get("model", "gpt-4o-mini"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return httpx.Response(200, json=payload)

        if url.endswith("/embeddings"):
            payload = {
                "object": "list",
                "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}],
                "model": json.get("model", "text-embedding-3-small"),
                "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
            }
            return httpx.Response(200, json=payload)

        return httpx.Response(404, json={"error": "not found"})

    test_app.state.http_client.post = post
    test_app.state.anthropic_adapter = AnthropicAdapter(test_app.state.http_client)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", provider_presets(), ids=lambda x: str(x["provider"]))
async def test_provider_presets_chat_smoke(client, test_app, preset: dict[str, Any]):
    setattr(test_app.state.settings, "master_key", "mk-test")
    _install_mock_provider_post(test_app)

    key_record = next(iter(test_app.state._test_repo.records.values()))
    key_record.models = []

    provider = str(preset["provider"])
    model_name = f"chat-smoke-{provider}"
    if provider == "anthropic":
        upstream_model = "anthropic/claude-sonnet-4-20250514"
    elif provider == "bedrock":
        upstream_model = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
    else:
        upstream_model = f"{provider}/gpt-4o-mini"

    create_response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json=_deployment_payload(model_name=model_name, provider=provider, mode="chat", upstream_model=upstream_model),
    )
    assert create_response.status_code == 200, create_response.text

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": model_name, "messages": [{"role": "user", "content": "hello"}], "stream": False},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", provider_presets(), ids=lambda x: str(x["provider"]))
async def test_provider_presets_embedding_smoke_with_capability_gate(client, test_app, preset: dict[str, Any]):
    setattr(test_app.state.settings, "master_key", "mk-test")
    _install_mock_provider_post(test_app)

    key_record = next(iter(test_app.state._test_repo.records.values()))
    key_record.models = []

    provider = str(preset["provider"])
    model_name = f"embed-smoke-{provider}"
    upstream_model = f"{provider}/text-embedding-3-small"

    create_response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json=_deployment_payload(model_name=model_name, provider=provider, mode="embedding", upstream_model=upstream_model),
    )

    if provider_supports_mode(provider, "embedding"):
        assert create_response.status_code == 200, create_response.text
        response = await client.post(
            "/v1/embeddings",
            headers={"Authorization": f"Bearer {test_app.state._test_key}"},
            json={"model": model_name, "input": "hello"},
        )
        assert response.status_code == 200, response.text
        return

    assert create_response.status_code == 400
    assert "does not support mode" in create_response.text
