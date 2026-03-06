from __future__ import annotations

import asyncio

import httpx
import pytest

from src.callbacks import CallbackManager, CustomLogger
from src.guardrails.base import CustomGuardrail, GuardrailAction
from src.guardrails.exceptions import GuardrailViolationError


class BlockingChatGuardrail(CustomGuardrail):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        del user_api_key_dict, cache, data, call_type
        raise GuardrailViolationError(self.name, "blocked by policy", "content_policy")


class LoggingChatGuardrail(BlockingChatGuardrail):
    def __init__(self, name: str):
        super().__init__(name=name, action=GuardrailAction.LOG)


class RecordingCallback(CustomLogger):
    def __init__(self):
        self.success = 0
        self.failure = 0

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        del kwargs, response_obj, start_time, end_time
        self.success += 1

    async def async_log_failure_event(self, kwargs, exception, start_time, end_time):
        del kwargs, exception, start_time, end_time
        self.failure += 1


@pytest.mark.asyncio
async def test_chat_completion_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers.get("x-deltallm-route-group") == "gpt-4o-mini"
    assert response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert response.headers.get("x-deltallm-route-deployment")
    assert response.headers.get("x-deltallm-route-fallback-used") == "false"
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_completion_streaming_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get("x-deltallm-route-group") == "gpt-4o-mini"
    assert response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert "data: [DONE]" in response.text


@pytest.mark.asyncio
async def test_chat_completion_guardrail_blocks(client, test_app):
    test_app.state.guardrail_registry.register(BlockingChatGuardrail(name="block-chat", default_on=True))
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "guardrail_violation"
    assert payload["error"]["guardrail"] == "block-chat"


@pytest.mark.asyncio
async def test_chat_completion_guardrail_log_mode_allows_request(client, test_app):
    test_app.state.guardrail_registry.register(LoggingChatGuardrail(name="log-chat"))
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completion_runs_success_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="success")
    test_app.state.callback_manager = manager

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.success == 1


@pytest.mark.asyncio
async def test_chat_completion_runs_failure_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="failure")
    test_app.state.callback_manager = manager

    async def failing_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        import httpx

        del headers, json, timeout
        return httpx.Response(503, json={"error": "unavailable"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = failing_post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 503
    await asyncio.sleep(0.05)
    assert recorder.failure == 1


class _StreamContext:
    def __init__(self, status_code: int, lines: list[str]) -> None:
        self.status_code = status_code
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_stream_retries_before_first_token_with_failover(client, test_app):
    registry = test_app.state.router.deployment_registry["gpt-4o-mini"]
    registry.append(
        type(registry[0])(
            deployment_id="gpt-4o-mini-fallback",
            model_name="gpt-4o-mini",
            deltallm_params={"model": "openai/gpt-4o-mini", "api_key": "provider-key-fallback"},
            model_info={},
        )
    )

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del request_context
        return test_app.state.router.deployment_registry[model_group][0]

    test_app.state.router.select_deployment = choose_primary

    calls = {"count": 0}

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        calls["count"] += 1
        auth = headers.get("Authorization", "")
        if auth.endswith("provider-key"):
            return _StreamContext(status_code=503, lines=[])
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-fb","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
                'data: [DONE]',
            ],
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_chat_completion_rejects_unsupported_provider(client, test_app):
    registry = test_app.state.router.deployment_registry["gpt-4o-mini"]
    registry[0].deltallm_params["provider"] = "xai"

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 400
    payload = response.json()
    assert "Unsupported provider" in payload.get("error", {}).get("message", "")


@pytest.mark.asyncio
async def test_chat_completion_uses_azure_api_key_header_when_provider_is_azure(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "azure_openai"
    deployment.deltallm_params["api_base"] = "https://azure.example/openai/v1"
    deployment.deltallm_params["api_key"] = "azure-provider-key"

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        assert url.endswith("/chat/completions")
        assert headers.get("api-key") == "azure-provider-key"
        assert "Authorization" not in headers
        payload = {
            "id": "chatcmpl-azure",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completion_does_not_forward_internal_metadata_upstream(client, test_app):
    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        assert "metadata" not in json
        payload = {
            "id": "chatcmpl-no-metadata",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "metadata": {"prompt_ref": {"template_key": "support.prompt", "label": "production"}},
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completion_uses_gemini_native_endpoint(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "gemini"
    deployment.deltallm_params["model"] = "gemini/gemini-2.5-flash"
    deployment.deltallm_params["api_base"] = "https://generativelanguage.googleapis.com/v1beta"
    deployment.deltallm_params["api_key"] = "gemini-key"

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout, headers
        assert "/models/gemini-2.5-flash:generateContent?key=gemini-key" in url
        assert "contents" in json
        payload = {
            "responseId": "resp_123",
            "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_completion_rejects_gemini_streaming_for_now(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "gemini"
    deployment.deltallm_params["model"] = "gemini/gemini-2.5-flash"
    deployment.deltallm_params["api_key"] = "gemini-key"

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 400
    assert "not supported yet" in response.text


@pytest.mark.asyncio
async def test_chat_completion_uses_bedrock_sigv4_headers(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "bedrock"
    deployment.deltallm_params["model"] = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
    deployment.deltallm_params["region"] = "us-east-1"
    deployment.deltallm_params["aws_access_key_id"] = "AKIDEXAMPLE"
    deployment.deltallm_params["aws_secret_access_key"] = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del timeout, json
        assert "/model/anthropic.claude-3-5-sonnet-20240620-v1:0/converse" in url
        assert content is not None
        assert headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 ")
        assert headers.get("X-Amz-Date")
        assert headers.get("X-Amz-Content-Sha256")
        payload = {
            "requestId": "req_123",
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"
