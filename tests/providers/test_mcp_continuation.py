from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest

from src.chat.mcp_execution import MCPChatExecutionService
from src.mcp.orchestrator import MCPChatOrchestrator
from src.models.requests import FunctionToolDefinition
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.profiled_chat import ProfiledChatAdapter
from tests.mcp.test_chat_execution import (
    _RecordingAuditService,
    _RecordingGateway,
    _auth,
    _request_context,
    _request_payload,
    _routing,
    _tool_call_response,
)


@pytest.mark.parametrize("hops", [1, 2])
async def test_real_mcp_service_keeps_tools_and_reasoning(test_app, contract, hops):
    gateway = _RecordingGateway()
    audit = _RecordingAuditService()
    calls = []
    reasoning = {
        "reasoning_content": "Keep this thought",
        "reasoning_details": [
            {"type": "reasoning.encrypted", "data": {"signature": "opaque", "value": None}}
        ],
    }

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        assert all(tool["type"] == "function" for tool in body["tools"])
        assert "temperature" not in body and "top_p" not in body
        histories = [message for message in body["messages"] if message["role"] == "assistant"]
        assert len(histories) == len(calls) - 1
        for index, message in enumerate(histories):
            assert message["content"] is None
            assert message["reasoning_content"] == reasoning["reasoning_content"]
            assert message["reasoning_details"] == reasoning["reasoning_details"]
            assert message["tool_calls"][0]["id"] == f"call-{index + 1}"
        if len(calls) <= hops:
            response = deepcopy(_tool_call_response())
            response["choices"][0]["message"].update({"content": None, **reasoning})
            response["choices"][0]["message"]["tool_calls"][0]["id"] = f"call-{len(calls)}"
            return httpx.Response(200, json=response)
        return httpx.Response(200, json=contract["success"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])

        async def execute(payload, deployment):
            body = await adapter.translate_request(
                payload, {"provider": contract["provider"], "model": contract["model"]}
            )
            response = await client.post("https://provider.example/chat/completions", json=body)
            result = await adapter.translate_response(response.json(), "alias")
            return result.model_dump(mode="json"), 1.0

        service = MCPChatExecutionService(
            failover_manager=test_app.state.failover_manager,
            orchestrator=MCPChatOrchestrator(gateway, audit_service=audit),
            execute_chat_call=execute,
        )
        payload = _request_payload()
        payload = payload.model_copy(
            update={
                "tools": [
                    *payload.tools,
                    FunctionToolDefinition(
                        function={"name": "client_tool", "parameters": {"type": "object"}}
                    ),
                ]
            }
        )
        await service.execute(
            request_context=_request_context("reasoning-test"),
            auth=_auth(),
            payload=payload,
            guardrail_middleware=test_app.state.guardrail_middleware,
            routing=_routing(test_app),
        )
    assert len(calls) == hops + 1
    assert gateway.tool_calls == ["docs.search"] * hops
    assert [getattr(event, "status", None) for event in audit.records] == [
        "attempted",
        "success",
    ] * hops
