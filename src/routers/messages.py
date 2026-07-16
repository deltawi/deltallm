from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.models.requests import AnthropicMessagesRequest
from src.routers.anthropic_adapters import (
    AnthropicStreamTranslator,
    anthropic_messages_to_chat_request,
    chat_response_to_anthropic_response,
)
from src.routers.chat import handle_chat_like_request

router = APIRouter(prefix="/v1", tags=["messages"])


@router.post("/messages", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def messages(request: Request, payload: AnthropicMessagesRequest):
    canonical = anthropic_messages_to_chat_request(payload)
    translator = AnthropicStreamTranslator(model=payload.model)
    return await handle_chat_like_request(
        request,
        canonical,
        response_transform=chat_response_to_anthropic_response,
        stream_line_transform=translator.translate_line,
        stream_response_object="message",
        enable_stream_cache=False,
    )
