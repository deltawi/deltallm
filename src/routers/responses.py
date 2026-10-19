from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from src.middleware.auth import require_api_key
from src.models.errors import InvalidRequestError, ProxyError
from src.models.requests import ResponsesRequest
from src.providers.anthropic_messages import NativeSSEFrame
from src.routers.chat import handle_chat_like_request
from src.routers.responses_adapters import (
    ResponsesShape,
    ResponsesStreamTranslator,
    chat_response_to_responses_response,
)
from src.routers.responses_request_view import ResponsesPolicyView, build_responses_policy_view

router = APIRouter(prefix="/v1", tags=["responses"])

_REQUEST_BODY_DOC = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": ResponsesRequest.model_json_schema()}},
    }
}


async def read_responses_request(request: Request) -> tuple[ResponsesRequest, dict[str, Any]]:
    """Parse the raw body once; the native hop forwards it and the view reads it."""

    # Always consume the body: the selector's disconnect watch expects the endpoint
    # to have read it, as FastAPI does for a declared body parameter.
    raw = await request.body()
    request_data = getattr(request.state, "request_data", None)
    if isinstance(request_data, dict):
        body = request_data
    else:
        try:
            body = json.loads(raw)
        except (JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidRequestError(message="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise InvalidRequestError(message="Request body must be a JSON object")
    try:
        return ResponsesRequest.model_validate(body), body
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def responses_policy_view(payload: ResponsesRequest, body: dict[str, Any]) -> ResponsesPolicyView:
    try:
        return build_responses_policy_view(payload, body)
    except ValidationError as exc:
        # The chat view rejects a tool or message the chat contract cannot hold.
        raise RequestValidationError(exc.errors()) from exc


@router.post("/responses", dependencies=[Depends(require_api_key)], openapi_extra=_REQUEST_BODY_DOC)
async def responses(request: Request):
    payload, body = await read_responses_request(request)
    view = responses_policy_view(payload, body)
    shape = ResponsesShape.from_body(body, view.tools)
    translator = ResponsesStreamTranslator(shape)

    def stream_line(line: str) -> str | None:
        # Native frames already carry the provider's own event; relay it unchanged.
        if isinstance(line, NativeSSEFrame):
            if line.native:
                translator.follow_native(line.native)
            return line.native or None
        return translator.translate_line(line)

    def stream_error(exc: ProxyError) -> str:
        return translator.error_event(exc)

    canonical = view.canonical
    if canonical.stream:
        # `response.completed` always reports usage, so the canonical stream keeps it.
        canonical.stream_options = {"include_usage": True}
    return await handle_chat_like_request(
        request,
        canonical,
        response_transform=lambda chat_payload: chat_response_to_responses_response(
            chat_payload, shape
        ),
        stream_line_transform=stream_line,
        stream_error_transform=stream_error,
        stream_response_object="response",
        enable_stream_cache=False,
        native_request=view.native_request,
    )
