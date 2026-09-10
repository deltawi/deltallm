#!/usr/bin/env python3
"""Measure the chat executor seam with the existing constant-arrival harness.

Run with PYTHONPATH pointing at the checkout to measure. This supports an old
checkout with --provider openai, so the same workload measures before and after.
HTTP is mocked with a fixed 1 ms delay. This is not full gateway certification:
admission, routing, Redis/SQL, and accounting are outside the measured boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from src.main import create_app
from src.chat.executor import execute_chat, open_stream_with_first_chunk
from src.models.requests import ChatCompletionRequest
from src.providers.openai import OpenAIAdapter
from src.providers.base import ProviderAdapter
from src.router.router import Deployment
from src.guardrails.middleware import GuardrailMiddleware
from src.guardrails.registry import GuardrailRegistry
from src.mcp.models import MCPToolCallResult, NamespacedTool
from src.mcp.orchestrator import MCPChatOrchestrator, MCPRequestContext
from src.models.responses import UserAPIKeyAuth


class MeasurementGateway:
    """Fixed local tool; no external MCP server or side effects."""

    calls = 0

    async def list_visible_tools(self, auth: UserAPIKeyAuth) -> list[NamespacedTool]:
        return [
            NamespacedTool(
                server_key="fixed",
                original_name="lookup",
                namespaced_name="fixed.lookup",
                input_schema={"type": "object"},
            )
        ]

    async def call_tool(
        self,
        auth: UserAPIKeyAuth,
        *,
        namespaced_tool_name: str,
        arguments: dict[str, object],
        request_headers: object = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MCPToolCallResult:
        self.calls += 1
        return MCPToolCallResult(content=[{"type": "text", "text": "x"}])


class MeasurementAudit:
    def record_event(
        self, event: object, *, payloads: object = None, critical: bool = False
    ) -> None:
        pass


def mcp_mock_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert all(tool["type"] == "function" for tool in body["tools"])
    previous = [message for message in body["messages"] if message["role"] == "assistant"]
    if previous:
        assert previous[0]["reasoning_content"] == "fixed thought"
        assert previous[0]["reasoning_details"] == [{"signature": "fixed", "data": None}]
        return mock_response(False)
    response = mock_response(False).json()
    response["choices"][0] = {
        "index": 0,
        "finish_reason": "tool_calls",
        "message": {
            "role": "assistant",
            "content": None,
            "reasoning_content": "fixed thought",
            "reasoning_details": [{"signature": "fixed", "data": None}],
            "tool_calls": [
                {
                    "id": "fixed-call",
                    "type": "function",
                    "function": {"name": "fixed.lookup", "arguments": "{}"},
                }
            ],
        },
    }
    return httpx.Response(200, json=response)


def mock_response(stream: bool) -> httpx.Response:
    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    common = {"id": "chatcmpl-fixed", "created": 1, "model": "fixed-model"}
    if not stream:
        return httpx.Response(
            200,
            json={
                **common,
                "object": "chat.completion",
                "usage": usage,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "x"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    frames = [
        {**common, "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}]},
        {**common, "usage": usage, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    return httpx.Response(
        200, text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
    )


@dataclass
class MeasurementAdapters:
    """One configured adapter, compatible with both historical and shared resolvers."""

    compatible_chat: dict[str, ProviderAdapter]

    def resolve(self, provider: str) -> ProviderAdapter | None:
        return self.compatible_chat.get(provider)


def configure_app(upstream: httpx.AsyncClient, provider: str) -> FastAPI:
    app = create_app()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app.state.settings = SimpleNamespace(openai_base_url="https://fixed-provider.invalid/v1")
    app.state.http_client = upstream
    app.state.openai_adapter = OpenAIAdapter(upstream)
    adapter = app.state.openai_adapter
    if provider != "openai":
        # Lazy imports let this identical script measure the pre-change checkout.
        from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
        from src.providers.profiled_chat import ProfiledChatAdapter

        adapter = ProfiledChatAdapter(upstream, CHAT_PROVIDER_PROFILES[provider])
    app.state.provider_error_mapper_registry = MeasurementAdapters({provider: adapter})
    return app


async def measure(args: argparse.Namespace) -> None:
    calls = 0
    provider_seconds = 0.0
    closed = 0
    gateway = MeasurementGateway()
    orchestrator = MCPChatOrchestrator(gateway, audit_service=MeasurementAudit())
    guardrails = GuardrailMiddleware(GuardrailRegistry())
    auth = UserAPIKeyAuth(api_key="mock-only", models=["fixed-model"])

    async def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls, provider_seconds
        calls += 1
        started = perf_counter()
        await asyncio.sleep(0.001)
        provider_seconds += perf_counter() - started
        return mcp_mock_response(_request) if args.mcp else mock_response(args.stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        app = configure_app(upstream, args.provider)
        request = Request({"type": "http", "app": app, "headers": []})
        payload = ChatCompletionRequest(
            model="fixed-model", messages=[{"role": "user", "content": "x"}], stream=args.stream
        )
        if args.mcp:
            payload = ChatCompletionRequest.model_validate(
                {
                    **payload.model_dump(exclude_unset=True),
                    "tools": [{"type": "mcp", "server": "fixed", "require_approval": "never"}],
                }
            )
        deployment = Deployment(
            "fixed",
            "fixed-model",
            {
                "provider": args.provider,
                "model": "fixed-model",
                "api_key": "mock-only",
                "api_base": "https://fixed-provider.invalid/v1",
            },
        )

        async def execute(_index: int, _request_id: str) -> RequestResult:
            nonlocal closed
            if args.mcp:

                async def model_call(current: ChatCompletionRequest):
                    return await execute_chat(request, current, deployment, record_usage=False)

                await orchestrator.execute(
                    request_context=MCPRequestContext(
                        request_headers={},
                        request_id=_request_id,
                        correlation_id=_request_id,
                        client_ip=None,
                        user_agent=None,
                    ),
                    auth=auth,
                    payload=payload,
                    execute_chat_call=model_call,
                    guardrail_middleware=guardrails,
                )
                return RequestResult(status_code=200)
            if not args.stream:
                await execute_chat(request, payload, deployment, record_usage=False)
                return RequestResult(status_code=200)
            started = perf_counter()
            opened = await open_stream_with_first_chunk(request, payload, deployment)
            ttft = perf_counter() - started
            try:
                async for _line in opened.translated_stream:
                    pass
            finally:
                await opened.close()
            closed += int(opened.response.is_closed)
            return RequestResult(status_code=200, ttft_seconds=ttft)

        for index in range(25):
            await execute(index, "warmup")
        calls, provider_seconds, closed = 0, 0.0, 0
        gateway.calls = 0
        result = await run_constant_arrival(
            rate=args.rate, duration_seconds=args.duration, max_in_flight=100, request=execute
        )
    summary = summarize(result, target_rate=args.rate)
    summary.update(
        {
            "boundary": ("chat executor and MCP orchestrator" if args.mcp else "chat executor")
            + " with fixed MockTransport; excludes admission/accounting",
            "provider": args.provider,
            "stream": args.stream,
            "mcp": args.mcp,
            "tool_calls": gateway.calls,
            "http_calls": calls,
            "provider_mean_seconds": provider_seconds / max(calls, 1),
            "stream_close_count": closed,
        }
    )
    paths = write_results(result, summary, args.output)
    print(json.dumps({"summary": summary, "artifacts": [str(path) for path in paths]}))
    expected_calls = result.target_count * (2 if args.mcp else 1)
    if summary["success_count"] != result.target_count or calls != expected_calls:
        raise SystemExit("Request success or provider-call budget failed")
    if args.mcp and gateway.calls != result.target_count:
        raise SystemExit("MCP tool execution budget failed")
    if args.stream and closed != calls:
        raise SystemExit("Stream cleanup budget failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Measure one MCP reasoning/tool continuation through the orchestrator and executor",
    )
    parser.add_argument("--rate", type=float, default=50)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mcp and (args.stream or args.provider not in {"deepseek", "minimax"}):
        parser.error("--mcp requires non-streaming deepseek or minimax")
    asyncio.run(measure(args))
