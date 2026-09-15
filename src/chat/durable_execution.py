"""HTTP chat composition; batch execution retains its completion-outbox protocol."""

from typing import Any

from fastapi import Request

from src.chat.executor import OpenedStream, execute_chat, open_stream_with_first_chunk
from src.models.requests import ChatCompletionRequest
from src.router.router import Deployment
from src.telemetry.spend_operation import durable_provider_call


async def execute_durable_chat(
    request: Request, payload: ChatCompletionRequest, deployment: Deployment
) -> tuple[dict[str, Any], float]:
    return await durable_provider_call(
        request,
        model=payload.model,
        call_type="completion",
        deployment=deployment,
        execute=lambda: execute_chat(request, payload, deployment),
    )


async def open_durable_stream(
    request: Request, payload: ChatCompletionRequest, deployment: Deployment
) -> OpenedStream:
    return await durable_provider_call(
        request,
        model=payload.model,
        call_type="completion",
        deployment=deployment,
        execute=lambda: open_stream_with_first_chunk(request, payload, deployment),
    )
