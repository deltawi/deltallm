from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.models.errors import InvalidRequestError
from src.models.requests import AnthropicMessagesRequest
from src.routers.anthropic_adapters import (
    AnthropicStreamTranslator,
    anthropic_messages_to_chat_request,
    chat_response_to_anthropic_response,
)
from src.routers.chat import handle_chat_like_request

router = APIRouter(prefix="/v1", tags=["messages"])


def _anthropic_validation_error_response(exc: ValidationError) -> JSONResponse:
    first_error = exc.errors()[0]
    field = ".".join(str(part) for part in first_error["loc"])
    message = f"{field}: {first_error['msg']}" if field else first_error["msg"]
    return JSONResponse(
        status_code=400,
        content={"type": "error", "error": {"type": "invalid_request_error", "message": message}},
    )


@router.post("/messages", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def messages(request: Request):
    try:
        payload = AnthropicMessagesRequest.model_validate(await request.json())
    except ValidationError as exc:
        return _anthropic_validation_error_response(exc)

    try:
        canonical = anthropic_messages_to_chat_request(payload)
    except InvalidRequestError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"type": "error", "error": {"type": exc.error_type, "message": exc.message}},
        )
    translator = AnthropicStreamTranslator(model=payload.model)
    return await handle_chat_like_request(
        request,
        canonical,
        response_transform=chat_response_to_anthropic_response,
        stream_line_transform=translator.translate_line,
        stream_response_object="message",
        enable_stream_cache=False,
    )
