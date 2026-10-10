from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request, Response

from src.auth.external_errors import ExternalAuthError
from src.middleware.admin import require_authenticated
from src.middleware.platform_auth import get_configured_master_key, get_platform_auth_context

from src.api.external_auth_edge import (
    ExternalAuthRoute,
    correlation_id,
    external_runtime,
    external_request_schema,
    read_external_body,
    require_backend_assertion_request,
)
from src.auth.external_contracts import ExternalPurpose
from src.models.external_auth import (
    ExternalAssertionRequest,
    ExternalExchangeResponse,
    ExternalInferenceKeyRequest,
    ExternalInferenceKeyResponse,
)

router = APIRouter(prefix="/auth/external", tags=["external-auth"], route_class=ExternalAuthRoute)


@router.post(
    "/exchange",
    response_model=ExternalExchangeResponse,
    openapi_extra=external_request_schema(ExternalAssertionRequest),
)
async def exchange(request: Request) -> ExternalExchangeResponse:
    require_backend_assertion_request(request)
    payload = await read_external_body(request, ExternalAssertionRequest)
    runtime = external_runtime(request)
    correlation = correlation_id(request)
    return await runtime.assertion_operation(
        payload.assertion.get_secret_value(),
        correlation,
        purposes=(ExternalPurpose.EXCHANGE,),
        operation=lambda assertion: runtime.exchange.exchange(assertion, correlation),
    )


@router.post(
    "/revoke", status_code=204, openapi_extra=external_request_schema(ExternalAssertionRequest)
)
async def revoke(request: Request) -> Response:
    require_backend_assertion_request(request)
    payload = await read_external_body(request, ExternalAssertionRequest)
    runtime = external_runtime(request)
    correlation = correlation_id(request)
    await runtime.assertion_operation(
        payload.assertion.get_secret_value(),
        correlation,
        purposes=(ExternalPurpose.REVOKE, ExternalPurpose.SUSPEND),
        operation=lambda assertion: runtime.revocation.revoke(assertion, correlation),
    )
    return Response(status_code=204)


@router.post(
    "/inference-key",
    response_model=ExternalInferenceKeyResponse,
    openapi_extra=external_request_schema(ExternalInferenceKeyRequest),
    dependencies=[Depends(require_authenticated)],
)
async def select_inference_key(request: Request) -> ExternalInferenceKeyResponse:
    payload = await read_external_body(request, ExternalInferenceKeyRequest)
    context = get_platform_auth_context(request)
    if context is None or context.external_workspace is None:
        raise ExternalAuthError()
    master = get_configured_master_key(request)
    raw_key = payload.api_key.get_secret_value()
    if master and hmac.compare_digest(raw_key, master):
        raise ExternalAuthError()
    return await external_runtime(request).inference_keys.select(raw_key, context)
