"""HTTP composition of spend intent and frozen pricing; batch keeps its own owner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, TypeVar
from uuid import UUID, uuid4

from fastapi import Request

from src.billing.frozen_pricing import freeze_operation_pricing
from src.billing.accounting_protocol import (
    AccountingAttempt,
    AccountingAttribution,
    AccountingOperationHandle,
    AccountingReservation,
    ReserveDecision,
    request_fingerprint,
)
from src.billing.accounting_service import AccountingProtocolService
from src.billing.provider_allowance import (
    ProviderRequestBounds,
    conservative_provider_allowance,
)
from src.billing.spend_operations import (
    OperationAttempt,
    OperationHandle,
    OperationPrincipal,
    SpendOperationIntent,
    SpendPersistenceUnavailable,
)
from src.billing.spend_ingestion import SpendIngestionService
from src.billing.tier_pricing import PricingResolution, resolve_deployment_tier_pricing
from src.providers.resolution import resolve_provider
from src.models.responses import UserAPIKeyAuth
from src.models.errors import BudgetExceededError
from src.router.router import Deployment
from src.telemetry.event_identity import get_or_create_billing_event_id

T = TypeVar("T")


ProviderOperationHandle = OperationHandle | AccountingOperationHandle


def accounting_client(request: Request) -> AccountingProtocolService | None:
    value = getattr(request.app.state, "accounting_protocol_service", None)
    if isinstance(value, AccountingProtocolService):
        return value
    if value is not None or getattr(request.app.state, "accounting_protocol_enabled", False):
        raise SpendPersistenceUnavailable()
    return None


def operation_handle(request: Request) -> ProviderOperationHandle | None:
    value = getattr(request.state, "spend_operation_handle", None)
    if value is not None and not isinstance(
        value,
        (OperationHandle, AccountingOperationHandle),
    ):
        raise SpendPersistenceUnavailable()
    return value


def billing_write_context(request: Request) -> dict[str, object]:
    context: dict[str, object] = {"event_id": get_or_create_billing_event_id(request)}
    handle = operation_handle(request)
    if handle is not None:
        context["operation"] = handle
    return context


def operation_pricing(
    request: Request, *, auth: UserAPIKeyAuth, model: str, deployment: Deployment
) -> PricingResolution:
    snapshots = getattr(request.state, "spend_operation_pricing", {})
    found = snapshots.get((model, deployment.deployment_id))
    if found is not None:
        return found
    return resolve_deployment_tier_pricing(
        auth=auth,
        model=model,
        deployment=deployment,
        tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
        mode="sync",
    )


def _pricing_snapshot(pricing: PricingResolution) -> dict[str, str | int | bool | None]:
    # Only billing dimensions and version identifiers; never arbitrary model_info.
    result: dict[str, str | int | bool | None] = {
        "source": pricing.source,
        "currency": "USD",
        "rounding": "ROUND_HALF_EVEN:1e-18",
        "tier_version_id": pricing.tier_version_id,
        "tier_assignment_id": pricing.tier_assignment_id,
    }
    for view, fields in (
        ("customer", pricing.customer_model_info),
        ("provider", pricing.provider_model_info),
    ):
        for field in (
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_token_cache_hit",
            "output_cost_per_token_cache_hit",
            "input_cost_per_character",
            "output_cost_per_character",
            "input_cost_per_second",
            "output_cost_per_second",
            "input_cost_per_image",
            "output_cost_per_image",
            "input_cost_per_audio_token",
            "output_cost_per_audio_token",
            "cost_per_request",
        ):
            value = fields.get(field)
            if value is not None:
                result[f"{view}.{field}"] = str(value)
    catalog = pricing.catalog_token_pricing
    if catalog is not None:
        for field in (
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_token_cache_hit",
            "output_cost_per_token_cache_hit",
            "cost_per_request",
        ):
            value = getattr(catalog, field)
            if value is not None:
                result[f"catalog.{field}"] = str(value)
    return result


async def durable_provider_call(
    request: Request,
    *,
    model: str,
    call_type: str,
    deployment: Deployment,
    execute: Callable[[], Awaitable[T]],
    bounds: ProviderRequestBounds | None = None,
) -> T:
    service = getattr(request.app.state, "spend_tracking_service", None)
    accounting = accounting_client(request)
    if accounting is not None:
        return await _accounted_provider_call(
            request,
            service=service,
            accounting=accounting,
            model=model,
            call_type=call_type,
            deployment=deployment,
            execute=execute,
            bounds=bounds,
        )
    if not isinstance(service, SpendIngestionService) or service.operations is None:
        return await execute()
    if not service.worker_health.ready or service._closed:
        raise SpendPersistenceUnavailable()
    auth = request.state.user_api_key
    previous = operation_handle(request)
    if previous is not None and not isinstance(previous, OperationHandle):
        raise SpendPersistenceUnavailable()
    pricing = freeze_operation_pricing(
        operation_pricing(request, auth=auth, model=model, deployment=deployment)
    )
    try:
        attempt = OperationAttempt(
            deployment_id=deployment.deployment_id,
            provider=resolve_provider(deployment.deltallm_params),
            model=model,
            pricing=_pricing_snapshot(pricing),
        )
        principal = OperationPrincipal.model_validate(
            {name: getattr(auth, name, None) for name in OperationPrincipal.model_fields}
        )
        intent = SpendOperationIntent(
            principal=principal,
            model=model,
            call_type=call_type,
            started_at=previous.intent.started_at if previous else datetime.now(UTC),
            attempts=(*previous.intent.attempts, attempt) if previous else (attempt,),
        )
        handle = OperationHandle(
            event_id=UUID(get_or_create_billing_event_id(request)),
            owner_token=previous.owner_token if previous else uuid4(),
            intent=intent,
            expires_at=previous.expires_at
            if previous
            else datetime.now(UTC) + timedelta(minutes=15),
        )
    except (ValueError, TypeError):
        raise SpendPersistenceUnavailable() from None
    await service.operations.begin(
        handle,
        capacity=service.config.max_pending_events,
        max_attempts=service.config.max_attempts,
        expires_at=asyncio.get_running_loop().time() + 0.25,
    )
    request.state.spend_operation_handle = handle
    snapshots = getattr(request.state, "spend_operation_pricing", {})
    snapshots[(model, deployment.deployment_id)] = pricing
    request.state.spend_operation_pricing = snapshots
    return await execute()


async def _accounted_provider_call(
    request: Request,
    *,
    service: object,
    accounting: AccountingProtocolService,
    model: str,
    call_type: str,
    deployment: Deployment,
    execute: Callable[[], Awaitable[T]],
    bounds: ProviderRequestBounds | None,
) -> T:
    if (
        not isinstance(service, SpendIngestionService)
        or service.accounting is not accounting
        or service._closed
    ):
        raise SpendPersistenceUnavailable()
    auth = request.state.user_api_key
    previous = operation_handle(request)
    if previous is not None and not isinstance(previous, AccountingOperationHandle):
        raise SpendPersistenceUnavailable()
    pricing = freeze_operation_pricing(
        operation_pricing(request, auth=auth, model=model, deployment=deployment)
    )
    attempt = AccountingAttempt(
        deployment_id=deployment.deployment_id,
        provider=resolve_provider(deployment.deltallm_params),
        model=model,
        pricing_snapshot=_pricing_snapshot(pricing),
    )
    max_attempts = int(getattr(request.app.state, "accounting_max_provider_attempts", 3))
    if bounds is None:
        raise SpendPersistenceUnavailable()
    allowance = conservative_provider_allowance(
        pricing=pricing,
        model_info=deployment.model_info,
        call_type=call_type,
        bounds=bounds,
        max_attempts=max_attempts,
    )
    if previous is not None:
        request.state.spend_operation_handle = _reuse_accounting_allowance(
            previous,
            auth=auth,
            model=model,
            call_type=call_type,
            max_attempts=max_attempts,
            allowance=allowance,
            attempt=attempt,
        )
        _store_pricing_snapshot(request, model, deployment, pricing)
        return await execute()
    reservation = _provider_reservation(
        request,
        auth=auth,
        model=model,
        call_type=call_type,
        deployment=deployment,
        attempt=attempt,
        generation=accounting.generation,
        allowance=allowance,
    )
    request.state.spend_operation_handle = await admit_accounting_reservation(
        accounting, reservation=reservation, attempt=attempt
    )
    _store_pricing_snapshot(request, model, deployment, pricing)
    return await execute()


async def admit_accounting_reservation(
    accounting: AccountingProtocolService,
    *,
    reservation: AccountingReservation,
    attempt: AccountingAttempt,
) -> AccountingOperationHandle:
    try:
        permit = await accounting.reserve(reservation)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise SpendPersistenceUnavailable() from None
    if permit.decision is ReserveDecision.BUDGET_EXHAUSTED:
        raise BudgetExceededError()
    if permit.decision is not ReserveDecision.DISPATCH:
        raise SpendPersistenceUnavailable()
    if permit.dispatch_token is None or permit.accounting_partition is None:
        raise SpendPersistenceUnavailable()
    return AccountingOperationHandle(
        reservation=reservation,
        dispatch_token=permit.dispatch_token,
        accounting_partition=permit.accounting_partition,
        attempts=(attempt,),
    )


def _reuse_accounting_allowance(
    previous: AccountingOperationHandle,
    *,
    auth: UserAPIKeyAuth,
    model: str,
    call_type: str,
    max_attempts: int,
    allowance: Decimal,
    attempt: AccountingAttempt,
) -> AccountingOperationHandle:
    # The first durable acknowledgement is the ceiling for all provider attempts.
    if (
        previous.reservation.attribution.api_key != auth.api_key
        or previous.reservation.attribution.model != model
        or previous.reservation.attribution.call_type != call_type
        or len(previous.attempts) >= max_attempts
        or allowance > previous.reservation.allowance
    ):
        raise SpendPersistenceUnavailable()
    return previous.model_copy(update={"attempts": (*previous.attempts, attempt)})


def _provider_reservation(
    request: Request,
    *,
    auth: UserAPIKeyAuth,
    model: str,
    call_type: str,
    deployment: Deployment,
    attempt: AccountingAttempt,
    generation: int,
    allowance: Decimal,
) -> AccountingReservation:
    operation_id = UUID(get_or_create_billing_event_id(request))
    attribution = AccountingAttribution(
        api_key=auth.api_key,
        user_id=auth.user_id,
        team_id=auth.team_id,
        organization_id=auth.organization_id,
        owner_account_id=auth.owner_account_id,
        end_user_id=None,
        model=model,
        deployment_id=deployment.deployment_id,
        provider=attempt.provider,
        call_type=call_type,
    )
    fingerprint = request_fingerprint(
        operation_kind=call_type,
        payload={
            "operation_id": str(operation_id),
            "api_key": auth.api_key,
            "model": model,
            "path": request.url.path,
        },
    )
    return AccountingReservation(
        protocol_generation=generation,
        operation_id=operation_id,
        owner_token=uuid4(),
        request_fingerprint=fingerprint,
        attribution=attribution,
        allowance=allowance,
        pricing_snapshot=attempt.pricing_snapshot,
        audit_envelope=_reservation_audit_envelope(
            attribution,
            operation_id=operation_id,
            allowance=allowance,
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=14),
    )


def _store_pricing_snapshot(
    request: Request,
    model: str,
    deployment: Deployment,
    pricing: PricingResolution,
) -> None:
    snapshots = getattr(request.state, "spend_operation_pricing", {})
    snapshots[(model, deployment.deployment_id)] = pricing
    request.state.spend_operation_pricing = snapshots


def _reservation_audit_envelope(
    attribution: AccountingAttribution,
    *,
    operation_id: UUID,
    allowance: Decimal,
    action: Literal[
        "ACCOUNTING_PROVIDER_RESERVED", "ACCOUNTING_CACHE_RESERVED"
    ] = "ACCOUNTING_PROVIDER_RESERVED",
    cache_hit: bool = False,
) -> dict[str, object]:
    metadata: dict[str, str | bool] = {"allowance_exact": str(allowance)}
    if cache_hit:
        metadata["cache_hit"] = True
    event = {
        "action": action,
        "organization_id": attribution.organization_id,
        "actor_type": "api_key",
        "actor_id": attribution.user_id or attribution.api_key,
        "api_key": attribution.api_key,
        "resource_type": "model",
        "resource_id": attribution.model,
        "request_id": str(operation_id),
        "correlation_id": str(operation_id),
        "status": "success",
        "metadata": metadata,
        "event_id": f"{operation_id}:reservation",
    }
    payload = {"event": event, "payloads": [], "critical": True}
    return {
        "event_id": f"{operation_id}:reservation:audit",
        "record_type": "audit_event",
        "organization_id": attribution.organization_id,
        "payload": payload,
        "redacted_payload": payload,
    }
