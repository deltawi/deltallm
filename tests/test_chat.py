from __future__ import annotations

import asyncio

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
