"""Use one request-owned selector adapter with the shared accounting queues."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

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
from src.billing.accounting.accounting_snapshots import finalization_bytes
from src.billing.accounting.journal.accounting_terminal_preparation import (
    prepare_accounting_charge,
    prepare_accounting_not_dispatched,
    prepare_accounting_uncertain,
)
from src.billing.charges.operation_reservation import (
    BillingOperation,
    BillingOperationUnavailable,
    ComponentState,
    ReservedOperation,
    SoftSelectorOperation,
    token_price_allowance,
)
from src.billing.charges.selector_charge import AcceptedSelectorCharge, SELECTOR_CALL_TYPE
from src.billing.spend.spend_operations import SpendPersistenceUnavailable


class NativeSelectorBilling:
    """Bootstrap owns the service. The admitted request owns each small proof."""

    def __init__(self, accounting: AccountingProtocolService) -> None:
        self.accounting = accounting

    def bind(
        self, operation: SoftSelectorOperation, *, max_input_tokens: int
    ) -> NativeSelectorStore:
        return NativeSelectorStore(
            self.accounting, operation=operation, max_input_tokens=max_input_tokens
        )


class NativeSelectorStore:
    """There is no feature ledger, client, task, queue, or provider retry here."""

    def __init__(
        self,
        accounting: AccountingProtocolService,
        *,
        operation: SoftSelectorOperation,
        max_input_tokens: int,
    ) -> None:
        if (
            type(max_input_tokens) is not int
            or not 1 <= max_input_tokens < 2**31
            or operation.admission_allowance
            != token_price_allowance(
                operation.pricing, input_tokens=max_input_tokens, output_tokens=64
            )
        ):
            raise BillingOperationUnavailable()
        self.accounting, self.operation = accounting, operation
        self.max_input_tokens = max_input_tokens
        self.handle_json: bytes | None = None
        self.terminal_json: bytes | None = None
        self.receipt_fingerprint: str | None = None
        self.dispatched = False
        self.acknowledged = False
        self.lock = asyncio.Lock()

    async def reserve(self, operation: BillingOperation, *, expires_at: float) -> ReservedOperation:
        self._require(operation, expires_at)
        if self.handle_json is not None:
            raise BillingOperationUnavailable()
        reservation, attempt = self._reservation()
        async with asyncio.timeout_at(expires_at):
            handle = await admit_accounting_reservation(
                self.accounting, reservation=reservation, attempt=attempt
            )
        encoded = handle.model_dump_json().encode()
        if len(encoded) > 24 * 1024:
            raise BillingOperationUnavailable()
        self.handle_json = encoded
        return ReservedOperation(
            operation=operation,
            selector_state=ComponentState.RESERVED,
            answer_state=ComponentState.UNATTEMPTED,
        )

    def _reservation(self) -> tuple[AccountingReservation, AccountingAttempt]:
        operation = self.operation
        owner = operation.attribution
        identity = UUID(owner.component_event_id)
        attribution = AccountingAttribution(
            api_key=owner.api_key,
            user_id=owner.user_id,
            team_id=owner.team_id,
            organization_id=owner.organization_id,
            owner_account_id=owner.owner_account_id,
            end_user_id=owner.end_user_id,
            model=owner.model_group,
            deployment_id=owner.deployment_id,
            provider=owner.provider,
            call_type=SELECTOR_CALL_TYPE,
        )
        prices = operation.pricing.model_dump(mode="json")
        reservation = AccountingReservation(
            protocol_generation=self.accounting.generation,
            operation_id=identity,
            owner_token=operation.owner_token,
            request_fingerprint=request_fingerprint(
                operation_kind="selector-component", payload=operation.model_dump(mode="json")
            ),
            attribution=attribution,
            allowance=operation.admission_allowance,
            pricing_snapshot={
                "selector": prices,
                "parent_event_id": str(owner.operation_id),
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": 64,
            },
            audit_envelope=reservation_audit_envelope(
                attribution, operation_id=identity, allowance=operation.admission_allowance
            ),
            expires_at=min(operation.expires_at, datetime.now(UTC) + ACCOUNTING_RECOVERY_LIFETIME),
        )
        return reservation, AccountingAttempt(
            deployment_id=owner.deployment_id,
            provider=owner.provider,
            model=owner.model_group,
            pricing_snapshot=prices,
        )

    async def dispatch(
        self,
        operation: BillingOperation,
        *,
        component: Literal["selector", "answer"],
        expires_at: float,
    ) -> None:
        self._require(operation, expires_at)
        if component != "selector" or self.dispatched or self.terminal_json is not None:
            raise BillingOperationUnavailable()
        self._handle()
        self.dispatched = True

    async def unattempted(
        self,
        operation: BillingOperation,
        *,
        component: Literal["selector", "answer"],
        expires_at: float,
    ) -> None:
        self._require(operation, expires_at)
        if component != "selector" or self.dispatched:
            raise BillingOperationUnavailable()
        await self._release(expires_at)

    async def confirm_not_dispatched(
        self, operation: BillingOperation, *, expires_at: float
    ) -> None:
        self._require(operation, expires_at)
        if not self.dispatched:
            raise BillingOperationUnavailable()
        await self._release(expires_at)

    async def _release(self, expires_at: float) -> None:
        async with self.lock:
            if self.terminal_json is None:
                handle = self._handle()
                terminal = prepare_accounting_not_dispatched(
                    handle,
                    occurred_at=datetime.now(UTC),
                    audit_envelope=self._audit(handle, status="success"),
                    event_id=UUID(self.operation.attribution.component_event_id),
                )
                self._freeze(terminal)
            await self._submit(expires_at)

    async def accept_selector(
        self, operation: BillingOperation, charge: AcceptedSelectorCharge, *, expires_at: float
    ) -> None:
        self._require(operation, expires_at)
        if (
            not self.dispatched
            or charge.attribution != self.operation.attribution
            or charge.pricing != self.operation.pricing
        ):
            raise BillingOperationUnavailable()
        fingerprint = request_fingerprint(
            operation_kind="selector-receipt", payload=charge.model_dump(mode="json")
        )
        exceeded = (
            charge.usage.prompt_tokens > self.max_input_tokens
            or charge.usage.completion_tokens > 64
            or charge.customer_charge > self.operation.admission_allowance
        )
        async with self.lock:
            if self.terminal_json is not None and self.receipt_fingerprint != fingerprint:
                raise BillingOperationUnavailable()
            if self.terminal_json is None:
                handle = self._handle()
                terminal = (
                    self._uncertain(handle, "selector_ceiling_exceeded")
                    if exceeded
                    else (
                        prepare_accounting_charge(
                            handle,
                            payload=charge.spend_payload()
                            | {"request_id": str(handle.reservation.operation_id)},
                            occurred_at=charge.finished_at,
                            audit_envelope=self._audit(handle, status="success"),
                            event_id=UUID(charge.attribution.component_event_id),
                        )
                    )
                )
                self._freeze(terminal, fingerprint)
            await self._submit(expires_at)
        if exceeded:
            raise BillingOperationUnavailable()

    async def close(self) -> None:
        if self.handle_json is None or self.acknowledged:
            return
        async with self.lock:
            if self.terminal_json is None:
                handle = self._handle()
                terminal = (
                    self._uncertain(handle, "selector_usage_missing")
                    if self.dispatched
                    else (
                        prepare_accounting_not_dispatched(
                            handle,
                            occurred_at=datetime.now(UTC),
                            audit_envelope=self._audit(handle, status="success"),
                            event_id=UUID(self.operation.attribution.component_event_id),
                        )
                    )
                )
                self._freeze(terminal)
            await self._submit(asyncio.get_running_loop().time() + 0.25)

    def _handle(self) -> AccountingOperationHandle:
        if self.handle_json is None:
            raise BillingOperationUnavailable()
        model = (
            LocalAccountingHandle
            if self.accounting.requires_local_proof
            else AccountingOperationHandle
        )
        return model.model_validate_json(self.handle_json)

    def _require(self, operation: BillingOperation, expires_at: float) -> None:
        if operation != self.operation or asyncio.get_running_loop().time() >= expires_at:
            raise BillingOperationUnavailable()

    def _audit(self, handle: AccountingOperationHandle, *, status: str) -> dict[str, object]:
        return accounting_audit_envelope(
            handle,
            event_id=self.operation.attribution.component_event_id,
            status=status,
            metadata={"attempt_count": 1, "component_purpose": "selector:v1"},
        )

    def _uncertain(self, handle: AccountingOperationHandle, reason: str) -> AccountingFinalization:
        return prepare_accounting_uncertain(
            handle,
            reason=reason,
            occurred_at=datetime.now(UTC),
            audit_envelope=self._audit(handle, status="error"),
            event_id=UUID(self.operation.attribution.component_event_id),
        )

    def _freeze(self, terminal: AccountingFinalization, receipt: str | None = None) -> None:
        encoded = finalization_bytes(terminal)
        if len(encoded) > 24 * 1024:
            raise BillingOperationUnavailable()
        self.terminal_json, self.receipt_fingerprint = encoded, receipt

    async def _submit(self, expires_at: float) -> None:
        if self.acknowledged:
            return
        if self.terminal_json is None:
            raise BillingOperationUnavailable()
        terminal = AccountingFinalization.model_validate_json(self.terminal_json)
        try:
            async with asyncio.timeout_at(expires_at):
                reply = await self.accounting.finalize_operation(self._handle(), terminal)
        except (TimeoutError, SpendPersistenceUnavailable):
            raise BillingOperationUnavailable() from None
        if (
            reply.operation_id != terminal.operation_id
            or reply.protocol_generation != terminal.protocol_generation
            or reply.outcome != terminal.outcome
        ):
            raise BillingOperationUnavailable()
        self.acknowledged = True
