from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.models.requests import CompletionsRequest
from src.routers.chat import handle_chat_like_request
from src.routers.text_adapters import (
    chat_response_to_completions_response,
    completions_to_chat_request,
    stream_chat_to_completions_line,
)

router = APIRouter(prefix="/v1", tags=["completions"])


@router.post("/completions", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def completions(request: Request, payload: CompletionsRequest):
    canonical = completions_to_chat_request(payload)
    request_data = canonical.model_dump(exclude_none=True)
    return await handle_chat_like_request(
        request,
        canonical,
        request_data=request_data,
        response_transform=chat_response_to_completions_response,
        stream_line_transform=stream_chat_to_completions_line,
        stream_response_object="text_completion",
        enable_stream_cache=False,
    )
