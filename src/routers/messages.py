from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.middleware.errors import anthropic_error_payload, anthropic_error_response
from src.middleware.auth import require_api_key
from src.models.errors import InvalidRequestError, ModelNotFoundError, ProxyError
from src.models.requests import AnthropicCountTokensRequest, AnthropicMessagesRequest
from src.chat.message_token_count import count_message_input_tokens
from src.providers.anthropic_messages import NativeMessagesRequest, NativeSSEFrame
from src.router.runtime_generation import pin_routing_runtime_generation
from src.routers.anthropic_adapters import (
    AnthropicStreamTranslator,
    chat_response_to_anthropic_response,
)
from src.routers.anthropic_request_view import (
    build_messages_policy_view,
    count_tokens_upstream_body,
    reject_provider_account_references,
)
from src.routers.chat import handle_chat_like_request
from src.services.access.model_visibility import (
    ensure_model_allowed,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
    get_tier_policy_mode_from_app,
)
from src.services.admission.preflight_capacity import (
    acquire_preflight_capacity,
    release_preflight_capacity,
)
from src.upstream_http import build_upstream_request_timeout_for_request

router = APIRouter(prefix="/v1", tags=["messages"])

# Tells clients whether `input_tokens` came from the provider or a local estimate.
_COUNT_SOURCE_HEADER = "x-deltallm-token-count"


def _anthropic_stream_error_event(exc: ProxyError) -> str:
    payload = anthropic_error_payload(status_code=exc.status_code, message=exc.message)
    return f"event: error\ndata: {json.dumps(payload, separators=(',', ':'))}"


def _anthropic_validation_error_response(exc: ValidationError) -> JSONResponse:
    first_error = exc.errors()[0]
    field = ".".join(str(part) for part in first_error["loc"])
    message = f"{field}: {first_error['msg']}" if field else first_error["msg"]
    return anthropic_error_response(
        status_code=400,
        error_type="invalid_request_error",
        message=message,
    )


async def _json_object_body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        request_body = await request.json()
    except (JSONDecodeError, UnicodeDecodeError):
        return anthropic_error_response(
            status_code=400,
            error_type="invalid_request_error",
            message="Invalid JSON body",
        )
    if not isinstance(request_body, dict):
        return anthropic_error_response(
            status_code=400,
            error_type="invalid_request_error",
            message="Request body must be a JSON object",
        )
    return request_body


@router.post("/messages", dependencies=[Depends(require_api_key)])
async def messages(request: Request):
    request_body = await _json_object_body(request)
    if isinstance(request_body, JSONResponse):
        return request_body

    try:
        payload = AnthropicMessagesRequest.model_validate(request_body)
    except ValidationError as exc:
        return _anthropic_validation_error_response(exc)

    try:
        view = build_messages_policy_view(
            payload,
            request_body,
            anthropic_beta=request.headers.get("anthropic-beta"),
            anthropic_version=request.headers.get("anthropic-version"),
        )
    except InvalidRequestError as exc:
        return anthropic_error_response(
            status_code=exc.status_code,
            error_type=exc.error_type,
            message=exc.message,
        )
    canonical = view.canonical
    translator = AnthropicStreamTranslator(model=payload.model)

    def stream_line(line: str) -> str | None:
        # Native frames already carry the provider's own event; relay it unchanged.
        if isinstance(line, NativeSSEFrame):
            return line.native
        return translator.translate_line(line)

    if canonical.stream:
        # Messages always reports usage. Keep canonical evidence for its
        # translator, including the provider's usage-only terminal chunk.
        canonical.stream_options = {"include_usage": True}
    return await handle_chat_like_request(
        request,
        canonical,
        response_transform=chat_response_to_anthropic_response,
        stream_line_transform=stream_line,
        stream_error_transform=_anthropic_stream_error_event,
        stream_response_object="message",
        enable_stream_cache=False,
        native_messages=view.native_request,
    )


@router.post("/messages/count_tokens", dependencies=[Depends(require_api_key)])
async def count_message_tokens(request: Request):
    request_body = await _json_object_body(request)
    if isinstance(request_body, JSONResponse):
        return request_body
    try:
        payload = AnthropicCountTokensRequest.model_validate(request_body)
        reject_provider_account_references(request_body)
    except ValidationError as exc:
        return _anthropic_validation_error_response(exc)
    except InvalidRequestError as exc:
        return anthropic_error_response(
            status_code=exc.status_code, error_type=exc.error_type, message=exc.message
        )

    auth = request.state.user_api_key
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    await acquire_preflight_capacity(request, auth=auth)
    try:
        ensure_model_allowed(
            auth,
            payload.model,
            callable_target_grant_service=getattr(
                request.app.state, "callable_target_grant_service", None
            ),
            callable_target_grant_snapshot=routing_runtime.authorization_snapshot,
            creator_model_access_snapshot=routing_runtime.creator_model_access_snapshot,
            creator_route_group_access_snapshot=(
                routing_runtime.creator_route_group_access_snapshot
            ),
            tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
            policy_mode=get_callable_target_policy_mode_from_app(request.app),
            tier_policy_mode=get_tier_policy_mode_from_app(request.app),
            tier_policy_missing_service_mode=get_tier_policy_missing_service_mode_from_app(
                request.app
            ),
        )
        router = routing_runtime.router
        model_group = router.resolve_model_group(payload.model)
        deployments = router.deployment_registry.get(model_group)
        if deployments is None:
            raise ModelNotFoundError(
                message=f"Model '{model_group}' is not configured", code="model_not_found"
            )
        result = await count_message_input_tokens(
            deployments=deployments,
            native=NativeMessagesRequest(
                body=count_tokens_upstream_body(request_body),
                anthropic_beta=request.headers.get("anthropic-beta"),
                anthropic_version=request.headers.get("anthropic-version"),
            ),
            adapters=request.app.state.provider_error_mapper_registry,
            default_openai_base_url=request.app.state.settings.openai_base_url,
            upstream_timeout=lambda seconds: build_upstream_request_timeout_for_request(
                request, seconds
            ),
        )
    except InvalidRequestError as exc:
        return anthropic_error_response(
            status_code=exc.status_code, error_type=exc.error_type, message=exc.message
        )
    finally:
        await release_preflight_capacity(request)
    return JSONResponse(
        {"input_tokens": result.input_tokens},
        headers={_COUNT_SOURCE_HEADER: result.source},
    )
