from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.middleware.auth import require_api_key
from src.models.errors import InvalidRequestError
from src.models.requests import AnthropicMessagesRequest
from src.routers.anthropic_adapters import (
    AnthropicStreamTranslator,
    anthropic_messages_to_chat_request,
    chat_response_to_anthropic_response,
)
from src.routers.chat import handle_chat_like_request

router = APIRouter(prefix="/v1", tags=["messages"])


def _anthropic_error_response(*, status_code: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )


def _anthropic_validation_error_response(exc: ValidationError) -> JSONResponse:
    first_error = exc.errors()[0]
    field = ".".join(str(part) for part in first_error["loc"])
    message = f"{field}: {first_error['msg']}" if field else first_error["msg"]
    return _anthropic_error_response(status_code=400, error_type="invalid_request_error", message=message)


@router.post("/messages", dependencies=[Depends(require_api_key)])
async def messages(request: Request):
    try:
        request_body = await request.json()
    except (JSONDecodeError, UnicodeDecodeError):
        return _anthropic_error_response(
            status_code=400,
            error_type="invalid_request_error",
            message="Invalid JSON body",
        )

    try:
        payload = AnthropicMessagesRequest.model_validate(request_body)
    except ValidationError as exc:
        return _anthropic_validation_error_response(exc)

    try:
        canonical = anthropic_messages_to_chat_request(payload)
    except InvalidRequestError as exc:
        return _anthropic_error_response(
            status_code=exc.status_code,
            error_type=exc.error_type,
            message=exc.message,
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
