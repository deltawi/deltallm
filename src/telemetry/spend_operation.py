"""HTTP composition of spend intent and frozen pricing; batch keeps its own owner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypeVar
from uuid import UUID, uuid4, uuid5

from fastapi import Request

from src.billing.accounting.accounting_admission import (
    ACCOUNTING_RECOVERY_LIFETIME,
    admit_accounting_reservation,
    reservation_audit_envelope as _reservation_audit_envelope,
)
from src.billing.accounting.accounting_pricing import (
    accounting_pricing_snapshot as _pricing_snapshot,
)

from src.billing.pricing.frozen_pricing import freeze_operation_pricing
from src.billing.accounting.accounting_protocol import (
    AccountingAttempt,
    AccountingAttribution,
    AccountingOperationHandle,
    AccountingReservation,
    request_fingerprint,
)
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.charges.provider_allowance import (
    ProviderRequestBounds,
    conservative_provider_allowance,
)
from src.billing.spend.spend_operations import (
    OperationAttempt,
    OperationHandle,
    OperationPrincipal,
    SpendOperationIntent,
    SpendPersistenceUnavailable,
)
from src.billing.spend.spend_ingestion import SpendIngestionService
from src.billing.pricing.tier_pricing import PricingResolution, resolve_deployment_tier_pricing
from src.providers.resolution import resolve_provider
from src.models.responses import UserAPIKeyAuth
from src.router.router import Deployment
from src.telemetry.event_identity import get_or_create_billing_event_id
from src.telemetry.selector_decision import ProtectedSelectorDecision

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
        or len(previous.attempts) >= min(max_attempts, 128)
        or allowance > previous.reservation.allowance
    ):
        raise SpendPersistenceUnavailable()
    # A retry changes only the new attempt. Keep the accepted issue and its
    # identity; fully check and detach mutable pricing facts for the new attempt.
    try:
        checked_attempt = AccountingAttempt.model_validate(attempt.model_dump())
    except ValueError as exc:
        raise SpendPersistenceUnavailable() from exc
    return previous.model_copy(update={"attempts": (*previous.attempts, checked_attempt)})


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
    prices = dict(attempt.pricing_snapshot)
    decision = getattr(request.state, "route_decision", None)
    if isinstance(decision, dict) and isinstance(decision.get("selector"), dict):
        ProtectedSelectorDecision.model_validate(decision["selector"])
        prices["selector_event_id"] = str(uuid5(operation_id, "selector:v1"))
    return AccountingReservation(
        protocol_generation=generation,
        operation_id=operation_id,
        owner_token=uuid4(),
        request_fingerprint=fingerprint,
        attribution=attribution,
        allowance=allowance,
        pricing_snapshot=prices,
        audit_envelope=_reservation_audit_envelope(
            attribution,
            operation_id=operation_id,
            allowance=allowance,
        ),
        expires_at=datetime.now(UTC) + ACCOUNTING_RECOVERY_LIFETIME,
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
