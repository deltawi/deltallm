"""Admit a known cache charge through the same durable budget authority."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Request

from src.billing.accounting_protocol import (
    AccountingAttempt,
    AccountingAttribution,
    AccountingReservation,
    request_fingerprint,
)
from src.billing.frozen_pricing import freeze_operation_pricing
from src.billing.money import canonical_money
from src.billing.spend_ingestion import SpendIngestionService
from src.billing.spend_operations import SpendPersistenceUnavailable
from src.billing.tier_pricing import PricingResolution
from src.models.responses import UserAPIKeyAuth
from src.telemetry.event_identity import get_or_create_billing_event_id
from src.telemetry.spend_operation import (
    _pricing_snapshot,
    _reservation_audit_envelope,
    accounting_client,
    admit_accounting_reservation,
    operation_handle,
)


async def reserve_cached_charge(
    request: Request,
    *,
    auth: UserAPIKeyAuth,
    model: str,
    call_type: Literal["completion", "embedding"],
    pricing: PricingResolution,
    exact_charge: Decimal,
    provider: str,
    deployment_id: str,
) -> None:
    accounting = accounting_client(request)
    if accounting is None:
        return
    spend = request.app.state.spend_tracking_service
    if (
        not isinstance(spend, SpendIngestionService)
        or spend.accounting is not accounting
        or spend._closed
        or not accounting.worker_health.ready
        or operation_handle(request) is not None
    ):
        raise SpendPersistenceUnavailable()
    operation_id = UUID(get_or_create_billing_event_id(request))
    attribution = AccountingAttribution(
        api_key=auth.api_key,
        user_id=auth.user_id,
        team_id=auth.team_id,
        organization_id=auth.organization_id,
        owner_account_id=auth.owner_account_id,
        model=model,
        call_type=call_type,
        provider=provider,
        deployment_id=deployment_id,
    )
    reservation = _cached_reservation(
        operation_id=operation_id,
        attribution=attribution,
        pricing=pricing,
        exact_charge=exact_charge,
        generation=accounting.generation,
    )
    frozen = reservation.pricing_snapshot
    request.state.spend_operation_handle = await admit_accounting_reservation(
        accounting,
        reservation=reservation,
        attempt=AccountingAttempt(
            deployment_id=deployment_id,
            provider=provider,
            model=model,
            pricing_snapshot=frozen,
        ),
    )


def _cached_reservation(
    *,
    operation_id: UUID,
    attribution: AccountingAttribution,
    pricing: PricingResolution,
    exact_charge: Decimal,
    generation: int,
) -> AccountingReservation:
    frozen = _pricing_snapshot(freeze_operation_pricing(pricing))
    audit = _reservation_audit_envelope(
        attribution,
        operation_id=operation_id,
        allowance=exact_charge,
        action="ACCOUNTING_CACHE_RESERVED",
        cache_hit=True,
    )
    return AccountingReservation(
        protocol_generation=generation,
        operation_id=operation_id,
        owner_token=uuid4(),
        request_fingerprint=request_fingerprint(
            operation_kind="cache_charge",
            payload={
                "operation_id": str(operation_id),
                "api_key": attribution.api_key,
                "model": attribution.model,
                "charge_exact": str(exact_charge),
            },
        ),
        attribution=attribution,
        allowance=canonical_money(exact_charge),
        pricing_snapshot=frozen,
        audit_envelope=audit,
        expires_at=datetime.now(UTC) + timedelta(minutes=14),
    )
