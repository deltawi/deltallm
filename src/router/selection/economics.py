from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging

from pydantic import ValidationError

from src.billing.operation_reservation import (
    BillingOperationUnavailable,
    ComponentState,
    OperationReservation,
    OperationReservationStore,
    selector_receipt,
)
from src.billing.selector_charge import AcceptedSelectorCharge, SelectorTokenReceipt
from src.cache.execution_eligibility import ResponseCacheEligibility
from src.router.selection.contracts import (
    ReportedSelectorUsage,
    SelectorHopOutcome,
    SelectorInvariantError,
    SelectorModelHop,
    SelectorPrompt,
    SelectorUsage,
    UnattemptedSelectorUsage,
)

RECEIPT_CLEANUP_SECONDS = 0.25
logger = logging.getLogger(__name__)


class ReservedSelectorAdmission:
    """An isolated prerequisite supplied by the future authenticated execution edge.

    Neither this type nor a cache signal authorizes a principal. The edge must have
    completed canonical authentication, final-model policy and caller admission.
    The repository verifies the frozen key's durable ownership relationships again.
    """

    def __init__(
        self,
        *,
        store: OperationReservationStore,
        operation: OperationReservation,
        cache: ResponseCacheEligibility,
    ) -> None:
        self._store, self._operation, self._cache = store, operation, cache

    async def admit(self, *, expires_at: float) -> None:
        self._cache.require_provider_execution()
        result = await self._store.reserve(self._operation, expires_at=expires_at)
        if result.selector_state is not ComponentState.RESERVED:
            # A previous process might already have dispatched this exact operation.
            raise BillingOperationUnavailable()

    async def finish(self, usage: SelectorUsage, *, expires_at: float) -> None:
        if isinstance(usage, UnattemptedSelectorUsage):
            await self._store.unattempted(
                self._operation, component="selector", expires_at=expires_at
            )


class AccountedSelectorHop:
    """Dispatch intent and receipts are independent of the answer's eventual result."""

    def __init__(
        self,
        *,
        store: OperationReservationStore,
        operation: OperationReservation,
        hop: SelectorModelHop,
    ) -> None:
        self._store, self._operation, self._hop = store, operation, hop

    async def invoke(
        self, *, deployment_id: str, prompt: SelectorPrompt, expires_at: float
    ) -> SelectorHopOutcome:
        operation = self._operation
        if deployment_id != operation.attribution.deployment_id:
            raise SelectorInvariantError()
        started = datetime.now(UTC)
        await self._store.dispatch(operation, component="selector", expires_at=expires_at)
        outcome = await self._hop.invoke(
            deployment_id=deployment_id, prompt=prompt, expires_at=expires_at
        )
        if isinstance(outcome.usage, UnattemptedSelectorUsage):
            await self._store.confirm_not_dispatched(
                operation, expires_at=asyncio.get_running_loop().time() + RECEIPT_CLEANUP_SECONDS
            )
        if isinstance(outcome.usage, ReportedSelectorUsage):
            try:
                usage = SelectorTokenReceipt(
                    prompt_tokens=outcome.usage.prompt_tokens,
                    completion_tokens=outcome.usage.completion_tokens,
                    total_tokens=outcome.usage.total_tokens,
                    cached_input_tokens=outcome.usage.cached_input_tokens,
                )
                charge = selector_receipt(operation, usage=usage, started_at=started)
            except (ValueError, ValidationError):
                # Persisted dispatch intent remains uncertain; never invent a zero receipt.
                raise BillingOperationUnavailable() from None
            # Receipt durability has a bounded cleanup grace independent of provider time.
            await self._accept(charge)
        return outcome

    async def _accept(self, charge: AcceptedSelectorCharge) -> None:
        deadline = asyncio.get_running_loop().time() + RECEIPT_CLEANUP_SECONDS
        try:
            await self._store.accept_selector(self._operation, charge, expires_at=deadline)
        except asyncio.CancelledError:
            # One idempotent receipt retry after cancellation, within the SAME cleanup
            # deadline. There is no detached task and the provider is never replayed.
            try:
                await self._store.accept_selector(self._operation, charge, expires_at=deadline)
            except Exception:
                logger.warning("selector_receipt_pending_reconciliation")
            raise
