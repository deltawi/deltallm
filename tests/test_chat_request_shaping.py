from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest

from src.providers.request_shaping import parse_request_shaping
from src.upstream_http import OUTBOUND_PROXY_EXTENSION


def _shape(test_app, **fields: Any):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params.update(fields)
    # Deployments parse their shaping once; tests change params in place.
    deployment.request_shaping = parse_request_shaping(deployment.deltallm_params)
    return deployment


def _capture_post(test_app) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def post(url, headers, json, timeout, **kwargs):  # noqa: ANN001, ANN202
        del timeout
        calls.append({"url": url, "headers": dict(headers), "json": dict(json), **kwargs})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-shaped",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    test_app.state.http_client.post = post
    return calls


def _body(content: str = "hello", **extra: Any) -> dict[str, Any]:
    return {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": content}], **extra}


@pytest.mark.asyncio
async def test_chat_shapes_body_and_headers_without_leaking_client_auth(client, test_app):
    _shape(
        test_app,
        override_params={"reasoning_effort": "high"},
        drop_params=["logit_bias", "user"],
        forward_headers=["x-opencode-project", "x-not-sent"],
        extra_headers={"X-Project": "acme"},
        session_header="x-opencode-session",
    )
    calls = _capture_post(test_app)
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {test_app.state._test_key}",
            "x-opencode-session": "ses_123",
            "x-opencode-project": "proj_9",
            "cookie": "a=b",
        },
        json=_body(reasoning_effort="low", logit_bias={"1": 1}, user="u1", temperature=0.3),
    )
    assert response.status_code == 200, response.text
    sent = calls[-1]
    assert sent["json"]["reasoning_effort"] == "high"
    assert sent["json"]["temperature"] == 0.3
    assert "logit_bias" not in sent["json"] and "user" not in sent["json"]
    headers = {name.lower(): value for name, value in sent["headers"].items()}
    assert headers["x-opencode-session"] == "ses_123"
    assert headers["x-opencode-project"] == "proj_9"
    assert headers["x-project"] == "acme"
    assert "x-not-sent" not in headers and "cookie" not in headers
    # The provider key, never the client's gateway key.
    assert test_app.state._test_key not in headers["authorization"]
    assert "extensions" not in sent


@pytest.mark.asyncio
async def test_session_header_is_stable_per_conversation_when_client_sends_none(client, test_app):
    _shape(test_app, session_header="x-opencode-session")
    calls = _capture_post(test_app)
    auth = {"Authorization": f"Bearer {test_app.state._test_key}"}
    first_turn = _body("plan the refactor")
    later_turn = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": "plan the refactor"},
            {"role": "assistant", "content": "Step 1"},
            {"role": "user", "content": "continue"},
        ],
    }
    # The test key allows two requests per minute.
    for body in (first_turn, later_turn):
        response = await client.post("/v1/chat/completions", headers=auth, json=body)
        assert response.status_code == 200, response.text
    ids = [call["headers"]["x-opencode-session"] for call in calls]
    assert ids[0] == ids[1]
    assert UUID(ids[0]).version == 4


def test_conversation_session_id_separates_conversations_keys_and_explicit_ids():
    from types import SimpleNamespace

    from src.chat.request_shaping import _conversation_session_id
    from src.models.requests import ChatCompletionRequest

    def request(api_key: str) -> SimpleNamespace:
        return SimpleNamespace(state=SimpleNamespace(user_api_key=SimpleNamespace(api_key=api_key)))

    def payload(content: str) -> ChatCompletionRequest:
        return ChatCompletionRequest(model="m", messages=[{"role": "user", "content": content}])

    base = _conversation_session_id(request("k1"), {}, payload("task a"))
    assert base == _conversation_session_id(request("k1"), {}, payload("task a"))
    assert base != _conversation_session_id(request("k1"), {}, payload("task b"))
    assert base != _conversation_session_id(request("k2"), {}, payload("task a"))
    explicit = _conversation_session_id(request("k1"), {"x-session-id": "agent-7"}, payload("x"))
    assert explicit == _conversation_session_id(
        request("k1"), {"x-session-id": "agent-7"}, payload("y")
    )
    assert "agent-7" not in explicit
    assert _conversation_session_id(SimpleNamespace(state=None), {}, payload("a")) is None


@pytest.mark.asyncio
async def test_named_proxy_is_passed_to_the_upstream_client(client, test_app):
    _shape(test_app, outbound_proxy="corp")
    test_app.state.upstream_proxy_names = frozenset({"corp"})
    calls = _capture_post(test_app)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json=_body(),
    )
    assert response.status_code == 200, response.text
    assert calls[-1]["extensions"] == {OUTBOUND_PROXY_EXTENSION: "corp"}


@pytest.mark.asyncio
async def test_unknown_proxy_fails_closed_before_any_upstream_call(client, test_app):
    _shape(test_app, outbound_proxy="gone")
    test_app.state.upstream_proxy_names = frozenset({"corp"})
    calls = _capture_post(test_app)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json=_body(),
    )
    assert response.status_code == 503
    assert calls == []


@pytest.mark.asyncio
async def test_invalid_stored_shaping_fails_closed(client, test_app):
    from src.providers.request_shaping import RequestShapingError

    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.request_shaping = RequestShapingError("bad")
    calls = _capture_post(test_app)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json=_body(),
    )
    assert response.status_code == 503
    assert calls == []


class _Stream:
    def __init__(self) -> None:
        self.status_code = 200
        self.headers = httpx.Headers()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aiter_lines(self):
        yield (
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m",'
            '"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}'
        )
        yield "data: [DONE]"


@pytest.mark.asyncio
async def test_stream_shapes_headers_body_and_proxy(client, test_app):
    _shape(
        test_app,
        override_params={"top_k": 5},
        extra_headers={"X-Project": "acme"},
        outbound_proxy="corp",
    )
    test_app.state.upstream_proxy_names = frozenset({"corp"})
    calls: list[dict[str, Any]] = []

    def stream(method, url, headers, json, timeout, **kwargs):  # noqa: ANN001, ANN202
        del method, url, timeout
        calls.append({"headers": dict(headers), "json": dict(json), **kwargs})
        return _Stream()

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json=_body(stream=True),
    )
    assert response.status_code == 200, response.text
    assert calls[-1]["json"]["top_k"] == 5
    assert calls[-1]["headers"]["X-Project"] == "acme"
    assert calls[-1]["extensions"] == {OUTBOUND_PROXY_EXTENSION: "corp"}
