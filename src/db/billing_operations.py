from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import math
from decimal import Decimal
from typing import TYPE_CHECKING, AsyncIterator, Literal

from src.billing.money import money_string
from src.billing.operation_reservation import (
    BillingOperationUnavailable,
    ComponentState,
    OperationReservation,
    ReservedOperation,
)
from src.billing.selector_charge import AcceptedSelectorCharge

if TYPE_CHECKING:
    from prisma import Prisma

Component = Literal["selector", "answer"]
DB_BUDGET_SECONDS = 0.25


class BillingOperationRepository:
    """Reservation/intent extension of spend ingestion; never dispatches providers.

    Production bootstrap does not construct this prerequisite until PR 4. The
    existing spend owner must drive recovery and settle holds in its writer transaction.
    """

    def __init__(self, db: Prisma, *, max_pending_operations: int = 100_000) -> None:
        if not 1 <= max_pending_operations <= 100_000:
            raise ValueError("invalid billing operation capacity")
        self.db = db
        self.max_pending_operations = max_pending_operations

    @asynccontextmanager
    async def _transaction(self, expires_at: float) -> AsyncIterator[Prisma]:
        now = asyncio.get_running_loop().time()
        if not math.isfinite(expires_at) or expires_at <= now:
            raise BillingOperationUnavailable()
        remaining = min(expires_at - now, DB_BUDGET_SECONDS)
        try:
            async with asyncio.timeout(remaining):
                async with self.db.tx(
                    max_wait=timedelta(seconds=remaining), timeout=timedelta(seconds=remaining)
                ) as tx:
                    await tx.query_raw(
                        "SELECT set_config('statement_timeout',$1,true), "
                        "set_config('lock_timeout',$1,true)",
                        f"{max(1, int(remaining * 1000))}ms",
                    )
                    yield tx
        except Exception:
            # Billing errors, including its deadline, must never become selector defaults.
            raise BillingOperationUnavailable() from None

    async def reserve(
        self, operation: OperationReservation, *, expires_at: float
    ) -> ReservedOperation:
        async with self._transaction(expires_at) as tx:
            # Match settlement: operation -> canonical account rows -> capacity.
            # Unique insertion serializes duplicate IDs without a global lock.
            if not await self._insert(tx, operation):
                rows = await tx.query_raw(
                    "SELECT * FROM deltallm_billing_operations WHERE operation_id=$1 FOR UPDATE",
                    str(operation.attribution.operation_id),
                )
                if not rows:
                    raise BillingOperationUnavailable()
                return self._existing(rows[0], operation)
            await tx.execute_raw(
                "SELECT deltallm_adjust_operation_hold($1,$2::numeric)",
                str(operation.attribution.operation_id),
                money_string(operation.total_allowance),
            )
            capacity = await tx.query_raw(
                "UPDATE deltallm_telemetry_ingestion_capacity SET pending_count=pending_count+1 "
                "WHERE queue_name='billing_operations' AND pending_count<$1 RETURNING pending_count",
                self.max_pending_operations,
            )
            if not capacity:
                # Roll back insertion and every hold when shared capacity is unavailable.
                raise BillingOperationUnavailable()
        return ReservedOperation(
            operation=operation,
            selector_state=ComponentState.RESERVED,
            answer_state=ComponentState.RESERVED,
        )

    async def _insert(self, tx: Prisma, operation: OperationReservation) -> bool:
        owner = operation.attribution
        inserted = await tx.query_raw(
            "INSERT INTO deltallm_billing_operations "
            "(operation_id,owner_token,api_key,user_id,team_id,organization_id,model,snapshot,"
            "selector_event_id,selector_allowance,answer_allowance,expires_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10::numeric,$11::numeric,$12::timestamptz) "
            "ON CONFLICT (operation_id) DO NOTHING RETURNING operation_id",
            str(owner.operation_id),
            str(operation.owner_token),
            owner.api_key,
            owner.user_id,
            owner.team_id,
            owner.organization_id,
            owner.model_group,
            operation.model_dump_json(),
            owner.component_event_id,
            money_string(operation.selector.allowance),
            money_string(operation.answer.allowance),
            operation.expires_at.isoformat(),
        )
        return bool(inserted)

    @staticmethod
    def _existing(row: dict[str, object], operation: OperationReservation) -> ReservedOperation:
        # Frozen snapshots prevent a replay from changing attribution, price or allowance.
        if row["snapshot"] != operation.model_dump(mode="json"):
            raise BillingOperationUnavailable()
        return ReservedOperation(
            operation=operation,
            selector_state=ComponentState(str(row["selector_state"])),
            answer_state=ComponentState(str(row["answer_state"])),
        )

    async def dispatch(
        self, operation: OperationReservation, *, component: Component, expires_at: float
    ) -> None:
        state = _column(component, "state")
        async with self._transaction(expires_at) as tx:
            rows = await tx.query_raw(
                f"UPDATE deltallm_billing_operations SET {state}='dispatched',updated_at=NOW() "
                f"WHERE operation_id=$1 AND owner_token=$2 AND {state}='reserved' "
                "AND snapshot=$3::jsonb AND expires_at>NOW() RETURNING operation_id",
                str(operation.attribution.operation_id),
                str(operation.owner_token),
                operation.model_dump_json(),
            )
            if not rows:
                # Dispatch is deliberately non-replayable, including an ambiguous prior commit.
                raise BillingOperationUnavailable()

    async def unattempted(
        self, operation: OperationReservation, *, component: Component, expires_at: float
    ) -> None:
        state, allowance = _column(component, "state"), _column(component, "allowance")
        async with self._transaction(expires_at) as tx:
            rows = await tx.query_raw(
                f"UPDATE deltallm_billing_operations SET {state}='unattempted',updated_at=NOW() "
                f"WHERE operation_id=$1 AND owner_token=$2 AND snapshot=$3::jsonb AND {state}='reserved' RETURNING {allowance}",
                str(operation.attribution.operation_id),
                str(operation.owner_token),
                operation.model_dump_json(),
            )
            if rows and Decimal(str(rows[0][allowance])) > 0:
                await tx.execute_raw(
                    "SELECT deltallm_adjust_operation_hold($1,-$2::numeric)",
                    str(operation.attribution.operation_id),
                    str(rows[0][allowance]),
                )
            if rows:
                await tx.execute_raw(
                    "SELECT deltallm_recover_operation($1)", str(operation.attribution.operation_id)
                )

    async def accept_selector(
        self, operation: OperationReservation, charge: AcceptedSelectorCharge, *, expires_at: float
    ) -> None:
        if (
            charge.attribution != operation.attribution
            or charge.pricing != operation.selector.pricing
        ):
            raise BillingOperationUnavailable()
        exceeded = (
            charge.customer_charge > operation.selector.allowance
            or charge.usage.prompt_tokens > operation.selector.max_input_tokens
            or charge.usage.completion_tokens > operation.selector.max_output_tokens
        )
        await self._accept_receipt(
            operation, component="selector", payload=charge.spend_payload(), expires_at=expires_at
        )
        if exceeded:
            # Preserve authoritative usage even if a provider violates its quoted ceiling.
            # Stop further answer work, without discarding incurred cost or inventing a refund.
            raise BillingOperationUnavailable()

    async def confirm_not_dispatched(
        self, operation: OperationReservation, *, expires_at: float
    ) -> None:
        """Only a live provider owner with a proved pre-send rejection may use this."""
        async with self._transaction(expires_at) as tx:
            rows = await tx.query_raw(
                "UPDATE deltallm_billing_operations SET selector_state='unattempted',updated_at=NOW() "
                "WHERE operation_id=$1 AND owner_token=$2 AND snapshot=$3::jsonb AND selector_state='dispatched' "
                "RETURNING selector_allowance",
                str(operation.attribution.operation_id),
                str(operation.owner_token),
                operation.model_dump_json(),
            )
            if rows and operation.selector.allowance > 0:
                await tx.execute_raw(
                    "SELECT deltallm_adjust_operation_hold($1,-$2::numeric)",
                    str(operation.attribution.operation_id),
                    money_string(operation.selector.allowance),
                )
            if rows:
                await tx.execute_raw(
                    "SELECT deltallm_recover_operation($1)", str(operation.attribution.operation_id)
                )

    async def _accept_receipt(
        self,
        operation: OperationReservation,
        *,
        component: Component,
        payload: dict[str, object],
        expires_at: float,
    ) -> None:
        state, receipt = _column(component, "state"), _column(component, "receipt")
        encoded = json.dumps(payload, default=str, sort_keys=True)
        async with self._transaction(expires_at) as tx:
            rows = await tx.query_raw(
                f"UPDATE deltallm_billing_operations SET {state}=CASE WHEN {state}='settled' THEN 'settled' ELSE 'accepted' END,{receipt}=$3::jsonb,updated_at=NOW() "
                f"WHERE operation_id=$1 AND owner_token=$2 AND snapshot=$4::jsonb AND ({state} IN ('dispatched','pending') "
                f"OR ({state} IN ('accepted','settled') AND {receipt}=$3::jsonb)) "
                "RETURNING operation_id",
                str(operation.attribution.operation_id),
                str(operation.owner_token),
                encoded,
                operation.model_dump_json(),
            )
            if not rows:
                raise BillingOperationUnavailable()


def _column(component: Component, field: Literal["state", "allowance", "receipt"]) -> str:
    columns = {
        ("selector", "state"): "selector_state",
        ("selector", "allowance"): "selector_allowance",
        ("selector", "receipt"): "selector_receipt",
        ("answer", "state"): "answer_state",
        ("answer", "allowance"): "answer_allowance",
        ("answer", "receipt"): "answer_receipt",
    }
    return columns[component, field]
