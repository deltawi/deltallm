"""Use the shared grant and terminal journal for Realtime turns."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from src.billing.accounting.accounting_admission import (
    ACCOUNTING_RECOVERY_LIFETIME,
    admit_accounting_reservation,
    reservation_audit_envelope,
)
from src.billing.accounting.accounting_finalization import accounting_audit_envelope
from src.billing.accounting.permits.accounting_local_leases import LocalAccountingHandle
from src.billing.accounting.accounting_protocol import (
    AccountingAttribution,
    AccountingAttempt,
    AccountingFinalization,
    AccountingOperationHandle,
    AccountingReservation,
    request_fingerprint,
)
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.journal.accounting_terminal_preparation import (
    prepare_accounting_charge,
    prepare_accounting_uncertain,
)
from src.billing.accounting.journal.accounting_turn_proofs import (
    AccountingTurnProof,
    AccountingTurnProofs,
)
from src.billing.charges.realtime_charge import RealtimeChargeContext
from src.billing.charges.realtime_accounting_bounds import RealtimeCostBounds
from src.billing.charges.realtime_usage import RealtimeUsageReceipt
from src.billing.spend.spend_operations import SpendPersistenceUnavailable
from src.db.realtime_billing import RealtimeBillingRepository
from src.realtime.errors import RealtimeError


class NativeRealtimeBilling:
    """Process memory holds replay inputs, not the durable money balance."""

    def __init__(
        self,
        accounting: AccountingProtocolService,
        identity: RealtimeBillingRepository,
        *,
        proofs: AccountingTurnProofs | None = None,
    ) -> None:
        self.accounting, self.identity = accounting, identity
        self.proofs = proofs if proofs is not None else AccountingTurnProofs()

    @property
    def requires_cost_bounds(self) -> bool:
        return True

    @property
    def terminal_lifetime(self) -> timedelta | None:
        return ACCOUNTING_RECOVERY_LIFETIME

    async def check_owner(self, context: RealtimeChargeContext) -> None:
        _require_bounds(context)
        await self.identity.check_owner(context, budget_holds=True)

    async def dispatch(
        self, operation_id: str, context: RealtimeChargeContext, *, expires_at: datetime
    ) -> None:
        bounds = _require_bounds(context)
        UUID(context.attribution.session_id)
        started_at = datetime.now(UTC)
        value = AccountingTurnProof(
            context.attribution.session_id, _context_id(context), started_at
        )
        self.proofs.begin(operation_id, value)
        try:
            owner = context.attribution
            attribution = AccountingAttribution(
                api_key=owner.api_key,
                user_id=owner.user_id,
                team_id=owner.team_id,
                organization_id=owner.organization_id,
                owner_account_id=owner.owner_account_id,
                model=owner.model,
                deployment_id=owner.deployment_id,
                provider="openai",
                call_type="realtime_transcription" if bounds.transcription else "realtime_response",
            )
            prices = {
                "source": "realtime-admission",
                "customer": context.customer.snapshot(),
                "provider": context.provider.snapshot(),
                "bounds": bounds.snapshot(),
            }
            attempt = AccountingAttempt(
                deployment_id=owner.deployment_id,
                provider="openai",
                model=owner.model,
                pricing_snapshot=prices,
            )
            allowance = bounds.allowance(context.customer)
            operation_uuid = UUID(operation_id)
            reservation = AccountingReservation(
                protocol_generation=self.accounting.generation,
                operation_id=operation_uuid,
                owner_token=uuid4(),
                request_fingerprint=request_fingerprint(
                    operation_kind="realtime-turn",
                    payload={"operation_id": operation_id, "context": _context_id(context)},
                ),
                attribution=attribution,
                allowance=allowance,
                pricing_snapshot=prices,
                audit_envelope=reservation_audit_envelope(
                    attribution, operation_id=operation_uuid, allowance=allowance
                ),
                expires_at=expires_at,
            )
            handle = await admit_accounting_reservation(
                self.accounting, reservation=reservation, attempt=attempt
            )
            self.proofs.retain_handle(value, handle)
        except BaseException:
            # An ambiguous issue remains funded in the shared owner. Never retry
            # provider dispatch with this operation or refund an unknown issue.
            self.proofs.remove(operation_id)
            raise

    async def accept(
        self, operation_id: str, context: RealtimeChargeContext, receipt: RealtimeUsageReceipt
    ) -> None:
        value = self.proofs.require(operation_id, _context_id(context))
        fingerprint = request_fingerprint(
            operation_kind="realtime-receipt", payload=asdict(receipt)
        )
        bounds = _require_bounds(context)
        expected = "transcription" if bounds.transcription else "response"
        if receipt.operation != expected:
            raise SpendPersistenceUnavailable()
        pending = (
            receipt.pending_reason is not None
            or receipt.usage is None
            or (not bounds.contains(receipt.usage))
        )
        async with value.lock:
            if (value.acknowledged or value.terminal_json is not None) and (
                value.receipt_fingerprint != fingerprint
            ):
                raise SpendPersistenceUnavailable()
            if not value.acknowledged:
                if value.terminal_json is None:
                    operation = self._operation(value)
                    terminal = _terminal(operation, context, receipt, started_at=value.started_at)
                    self.proofs.freeze_terminal(value, terminal, receipt=fingerprint)
                await self._submit(value)
            if pending:
                raise RealtimeError(
                    "usage_pending", "Realtime usage requires reconciliation", close_code=1011
                )

    async def close(self, session_id: str) -> None:
        pending = []
        for operation_id, value in self.proofs.session_proofs(session_id):
            if value.acknowledged:
                self.proofs.remove(operation_id)
            else:
                pending.append((operation_id, value))
        # Session cleanup owns these tasks and cancels them with its deadline.
        # At most eight terminal submissions can execute at once.
        for offset in range(0, len(pending), 8):
            await asyncio.gather(
                *(
                    self._close_turn(operation_id, value)
                    for operation_id, value in pending[offset : offset + 8]
                )
            )

    async def _close_turn(self, operation_id: str, value: AccountingTurnProof) -> None:
        async with value.lock:
            if not value.acknowledged:
                if value.terminal_json is None:
                    operation = self._operation(value)
                    terminal = _uncertain(operation, "terminal_usage_missing", datetime.now(UTC))
                    self.proofs.freeze_terminal(value, terminal, receipt=None)
                await self._submit(value)
            self.proofs.remove(operation_id)

    def _operation(self, value: AccountingTurnProof) -> AccountingOperationHandle:
        if value.handle_json is None:
            raise SpendPersistenceUnavailable()
        model = (
            LocalAccountingHandle
            if self.accounting.requires_local_proof
            else AccountingOperationHandle
        )
        return model.model_validate_json(value.handle_json)

    async def _submit(self, value: AccountingTurnProof) -> None:
        if value.terminal_json is None:
            raise SpendPersistenceUnavailable()
        operation = self._operation(value)
        terminal = AccountingFinalization.model_validate_json(value.terminal_json)
        reply = await self.accounting.finalize_operation(operation, terminal)
        if (
            reply.operation_id != terminal.operation_id
            or reply.protocol_generation != terminal.protocol_generation
            or reply.outcome != terminal.outcome
        ):
            raise SpendPersistenceUnavailable()
        self.proofs.acknowledge(value)


def _require_bounds(context: RealtimeChargeContext) -> RealtimeCostBounds:
    if context.cost_bounds is None:
        raise RealtimeError("budget_profile_unsupported", "Realtime has no qualified cost ceiling")
    return context.cost_bounds


def _context_id(context: RealtimeChargeContext) -> str:
    return request_fingerprint(operation_kind="realtime-context", payload=context.snapshot())


def _terminal(
    operation: AccountingOperationHandle,
    context: RealtimeChargeContext,
    receipt: RealtimeUsageReceipt,
    *,
    started_at: datetime,
) -> AccountingFinalization:
    now = datetime.now(UTC)
    if receipt.pending_reason is not None or receipt.usage is None:
        return _uncertain(operation, receipt.pending_reason or "usage_missing", now)
    if not _require_bounds(context).contains(receipt.usage):
        return _uncertain(operation, "usage_ceiling_exceeded", now)
    payload = context.spend_payload(
        receipt,
        operation_started_at=started_at,
        completed_at=now,
        operation_id=str(operation.reservation.operation_id),
    )
    event_id = uuid5(operation.reservation.operation_id, "provider-finalization:v2")
    return prepare_accounting_charge(
        operation,
        payload=payload,
        occurred_at=now,
        audit_envelope=accounting_audit_envelope(
            operation,
            event_id=event_id,
            status="success",
            metadata={"attempt_count": 1, "realtime": True},
        ),
    )


def _uncertain(
    operation: AccountingOperationHandle, reason: str, occurred_at: datetime
) -> AccountingFinalization:
    event_id = uuid5(operation.reservation.operation_id, "provider-finalization:v2")
    return prepare_accounting_uncertain(
        operation,
        reason=reason,
        occurred_at=occurred_at,
        audit_envelope=accounting_audit_envelope(
            operation,
            event_id=event_id,
            status="error",
            metadata={"attempt_count": 1, "uncertainty_reason": reason, "realtime": True},
        ),
    )
