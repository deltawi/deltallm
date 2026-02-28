from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.models.requests import ResponsesRequest
from src.routers.chat import handle_chat_like_request
from src.routers.text_adapters import (
    chat_response_to_responses_response,
    responses_to_chat_request,
    stream_chat_to_responses_line,
)

router = APIRouter(prefix="/v1", tags=["responses"])


@router.post("/responses", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def responses(request: Request, payload: ResponsesRequest):
    canonical = responses_to_chat_request(payload)
    request_data = canonical.model_dump(exclude_none=True)
    return await handle_chat_like_request(
        request,
        canonical,
        request_data=request_data,
        response_transform=chat_response_to_responses_response,
        stream_line_transform=stream_chat_to_responses_line,
        stream_response_object="response.output_text.delta",
        enable_stream_cache=False,
    )
