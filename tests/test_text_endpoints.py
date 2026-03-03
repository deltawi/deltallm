from __future__ import annotations

import pytest


class _AlwaysBudgetExceeded:
    async def check_budgets(self, **kwargs):
        del kwargs
        from src.billing.budget import BudgetExceeded
        raise BudgetExceeded(entity_type="key", entity_id="k1", spend=10.0, max_budget=5.0)


class _RecordingAuditService:
    def __init__(self) -> None:
        self.records: list[tuple[object, list[object], bool]] = []

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        self.records.append((event, list(payloads or []), critical))


@pytest.mark.asyncio
async def test_completions_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "prompt": "hello", "stream": False}

    response = await client.post("/v1/completions", headers=headers, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "text_completion"
    assert payload["choices"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_completions_stream_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "prompt": "hello", "stream": True}

    response = await client.post("/v1/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in response.text
    assert '"object":"text_completion"' in response.text


@pytest.mark.asyncio
async def test_completions_unsupported_field_returns_400(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "prompt": "hello", "echo": True}

    response = await client.post("/v1/completions", headers=headers, json=body)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_responses_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "input": "hello", "stream": False}

    response = await client.post("/v1/responses", headers=headers, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "response"
    assert payload["output"][0]["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_responses_stream_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "input": "hello", "stream": True}

    response = await client.post("/v1/responses", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in response.text
    assert '"object":"response.output_text.delta"' in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/chat/completions", {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}),
        ("/v1/completions", {"model": "gpt-4o-mini", "prompt": "hello"}),
        ("/v1/responses", {"model": "gpt-4o-mini", "input": "hello"}),
    ],
)
async def test_text_endpoints_budget_exceeded_returns_429(client, test_app, path, body):
    test_app.state.budget_service = _AlwaysBudgetExceeded()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "budget_exceeded"
    assert error["code"] == "budget_exceeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body", "expected_action"),
    [
        ("/v1/chat/completions", {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}, "CHAT_COMPLETION_REQUEST"),
        ("/v1/completions", {"model": "gpt-4o-mini", "prompt": "hello", "stream": False}, "COMPLETION_REQUEST"),
        ("/v1/responses", {"model": "gpt-4o-mini", "input": "hello", "stream": False}, "RESPONSES_REQUEST"),
        ("/v1/embeddings", {"model": "text-embedding-3-small", "input": "hello"}, "EMBEDDING_REQUEST"),
    ],
)
async def test_data_plane_routes_emit_audit_success(client, test_app, path, body, expected_action):
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit

    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-audit-success"}
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 200

    assert audit.records
    event, payloads, critical = audit.records[-1]
    assert event.action == expected_action
    assert event.status == "success"
    assert event.request_id == "req-audit-success"
    assert event.resource_id == body["model"]
    assert event.latency_ms >= 0
    assert critical is True
    assert payloads


@pytest.mark.asyncio
async def test_chat_stream_emits_audit_success(client, test_app):
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit
    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-stream-audit"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert "data: [DONE]" in response.text

    assert audit.records
    event, _, _ = audit.records[-1]
    assert event.action == "CHAT_COMPLETION_REQUEST"
    assert event.status == "success"
    assert event.request_id == "req-stream-audit"
