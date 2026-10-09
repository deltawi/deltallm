from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from src.api.external_auth_edge import (
    ExternalAuthRoute,
    correlation_id,
    external_runtime,
    external_request_schema,
    read_external_body,
)
from src.auth.external_contracts import ExternalPurpose
from src.db.external_auth_records import (
    ExternalBindingRecord,
    ExternalIntegrationRecord,
    ExternalSubjectRecord,
)
from src.middleware.admin import require_master_key
from src.middleware.platform_auth import get_platform_auth_context
from src.models.external_auth import (
    ExternalBindingRequest,
    ExternalAuthDiagnostics,
    ExternalIntegrationRequest,
    ExternalLinkRequest,
    ExternalRuntimeBindingRequest,
    ExternalVersionRequest,
)
from src.services.external_auth_linking import ExternalLinkApproval

router = APIRouter(
    prefix="/ui/api/external-auth",
    tags=["external-auth-admin"],
    route_class=ExternalAuthRoute,
    dependencies=[Depends(require_master_key)],
)

diagnostics_router = APIRouter(
    tags=["external-auth-admin"], dependencies=[Depends(require_master_key)]
)


@diagnostics_router.get("/ui/api/external-auth/status", response_model=ExternalAuthDiagnostics)
async def external_auth_diagnostics(request: Request) -> ExternalAuthDiagnostics:
    from src.services.external_auth_runtime import ExternalAuthRuntime

    runtime = getattr(request.app.state, "external_auth_runtime", None)
    if not isinstance(runtime, ExternalAuthRuntime):
        return ExternalAuthDiagnostics(
            protocol="external_customer_v1",
            state="disabled",
            cleanup_healthy=False,
            crypto_ready=False,
            cache_worker_ready=False,
            active_mutations=0,
            queued_mutations=0,
            active_validations=0,
            queued_validations=0,
        )
    return ExternalAuthDiagnostics(
        protocol="external_customer_v1",
        state="ready" if await runtime.check_ready() else "degraded",
        cleanup_healthy=runtime.cleanup_healthy,
        crypto_ready=runtime.crypto.ready,
        cache_worker_ready=runtime.cache_worker_ready(),
        active_mutations=runtime.transactions.gates["mutation"].active,
        queued_mutations=runtime.transactions.gates["mutation"].waiters,
        active_validations=runtime.transactions.gates["validation"].active,
        queued_validations=runtime.transactions.gates["validation"].waiters,
    )


def _actor(request: Request) -> str:
    context = get_platform_auth_context(request)
    return context.account_id if context is not None else "master_key"


@router.put(
    "/integrations/{integration_id}",
    response_model=ExternalIntegrationRecord,
    openapi_extra=external_request_schema(ExternalIntegrationRequest),
)
async def update_integration(integration_id: str, request: Request) -> ExternalIntegrationRecord:
    payload = await read_external_body(request, ExternalIntegrationRequest)
    return await external_runtime(request).administration.set_integration(
        integration_id,
        enabled=payload.enabled,
        request=payload,
        correlation_id=correlation_id(request),
        approved_by=_actor(request),
    )


@router.put(
    "/integrations/{integration_id}/bindings/{customer_id}",
    response_model=ExternalBindingRecord,
    openapi_extra=external_request_schema(ExternalBindingRequest),
)
async def register_binding(
    integration_id: str, customer_id: str, request: Request
) -> ExternalBindingRecord:
    payload = await read_external_body(request, ExternalBindingRequest)
    return await external_runtime(request).administration.register_binding(
        integration_id,
        customer_id=customer_id,
        organization_id=payload.organization_id,
        team_id=payload.team_id,
        correlation_id=correlation_id(request),
        approved_by=_actor(request),
    )


@router.get("/integrations/{integration_id}/bindings")
async def list_bindings(
    integration_id: str,
    request: Request,
    after: str | None = Query(None, max_length=200),
    state: str | None = Query(None, pattern="^(active|suspended)$"),
    customer: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, object]:
    rows = await external_runtime(request).administration.list_bindings(
        integration_id,
        after=after,
        state=state,
        customer=customer,
        limit=limit + 1,
    )
    return {
        "items": rows[:limit],
        "next_cursor": rows[limit - 1].binding_id if len(rows) > limit else None,
    }


async def _binding_state(binding_id: str, request: Request, active: bool) -> ExternalBindingRecord:
    payload = await read_external_body(request, ExternalVersionRequest)
    return await external_runtime(request).administration.set_binding(
        binding_id,
        active=active,
        request=payload,
        correlation_id=correlation_id(request),
        approved_by=_actor(request),
    )


@router.post(
    "/bindings/{binding_id}/suspend",
    response_model=ExternalBindingRecord,
    openapi_extra=external_request_schema(ExternalVersionRequest),
)
async def suspend_binding(binding_id: str, request: Request) -> ExternalBindingRecord:
    return await _binding_state(binding_id, request, False)


@router.post(
    "/bindings/{binding_id}/resume",
    response_model=ExternalBindingRecord,
    openapi_extra=external_request_schema(ExternalVersionRequest),
)
async def resume_binding(binding_id: str, request: Request) -> ExternalBindingRecord:
    return await _binding_state(binding_id, request, True)


@router.post(
    "/subjects/{subject_id}/resume",
    response_model=ExternalSubjectRecord,
    openapi_extra=external_request_schema(ExternalVersionRequest),
)
async def resume_subject(subject_id: str, request: Request) -> ExternalSubjectRecord:
    payload = await read_external_body(request, ExternalVersionRequest)
    return await external_runtime(request).administration.resume_subject(
        subject_id,
        request=payload,
        correlation_id=correlation_id(request),
        approved_by=_actor(request),
    )


@router.post(
    "/subjects/link",
    response_model=ExternalSubjectRecord,
    openapi_extra=external_request_schema(ExternalLinkRequest),
)
async def link_account(request: Request) -> ExternalSubjectRecord:
    payload = await read_external_body(request, ExternalLinkRequest)
    runtime = external_runtime(request)
    approval = ExternalLinkApproval(
        payload.binding_id,
        payload.runtime_user_id,
        payload.expected_version,
        payload.approval_reference,
        payload.reason,
        _actor(request),
        payload.account_id,
    )
    correlation = correlation_id(request)
    return await runtime.assertion_operation(
        payload.assertion.get_secret_value(),
        correlation,
        purposes=(ExternalPurpose.LINK,),
        operation=lambda assertion: runtime.linking.link(assertion, approval, correlation),
    )


@router.post(
    "/subjects/runtime-user-binding",
    response_model=ExternalSubjectRecord,
    openapi_extra=external_request_schema(ExternalRuntimeBindingRequest),
)
async def bind_runtime(request: Request) -> ExternalSubjectRecord:
    payload = await read_external_body(request, ExternalRuntimeBindingRequest)
    runtime = external_runtime(request)
    approval = ExternalLinkApproval(
        payload.binding_id,
        payload.runtime_user_id,
        payload.expected_version,
        payload.approval_reference,
        payload.reason,
        _actor(request),
    )
    correlation = correlation_id(request)
    return await runtime.assertion_operation(
        payload.assertion.get_secret_value(),
        correlation,
        purposes=(ExternalPurpose.RUNTIME_BIND,),
        operation=lambda assertion: runtime.linking.bind_runtime(assertion, approval, correlation),
    )
