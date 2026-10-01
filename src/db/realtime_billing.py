from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.billing.operation_reservation import BillingOperationUnavailable
from src.billing.realtime_charge import RealtimeChargeContext
from src.billing.realtime_usage import RealtimeUsageReceipt
from src.db.billing_transaction import billing_transaction
from src.realtime.errors import RealtimeError

if TYPE_CHECKING:
    from prisma import Prisma


class RealtimeBillingRepository:
    """Durable per-turn intent/receipt journal; settlement uses the spend owner."""

    def __init__(self, db: Prisma, *, max_pending: int = 100_000) -> None:
        if not 1 <= max_pending <= 100_000:
            raise ValueError("Invalid Realtime intent capacity")
        self.db = db
        self.max_pending = max_pending

    async def check_owner(self, context: RealtimeChargeContext) -> None:
        owner = context.attribution
        async with billing_transaction(self.db, _deadline()) as tx:
            rows = await tx.query_raw(
                """
                SELECT k.max_budget AS key_budget, u.max_budget AS user_budget,
                    t.max_budget AS team_budget, o.max_budget AS org_budget,
                    t.model_max_budget->>$6 AS model_budget
                FROM deltallm_verificationtoken k
                LEFT JOIN deltallm_usertable u ON u.user_id=$2
                LEFT JOIN deltallm_serviceaccount s ON s.service_account_id=k.owner_service_account_id
                LEFT JOIN deltallm_teamtable t ON t.team_id=$3
                LEFT JOIN deltallm_organizationtable o ON o.organization_id=$4
                WHERE k.token=$1 AND k.user_id IS NOT DISTINCT FROM $2::text
                    AND COALESCE(k.team_id,u.team_id,s.team_id) IS NOT DISTINCT FROM $3::text
                    AND k.owner_account_id IS NOT DISTINCT FROM $5::text
                    AND t.organization_id IS NOT DISTINCT FROM $4::text
                    AND ($2::text IS NULL OR (u.user_id IS NOT NULL AND u.blocked IS NOT TRUE))
                    AND ($3::text IS NULL OR (t.team_id IS NOT NULL AND t.blocked IS NOT TRUE))
                    AND ($4::text IS NULL OR (o.organization_id IS NOT NULL AND o.lifecycle_state='active'))
                    AND (k.owner_service_account_id IS NULL OR s.is_active IS TRUE)
                    AND (k.expires IS NULL OR k.expires>CURRENT_TIMESTAMP)
                """,
                owner.api_key,
                owner.user_id,
                owner.team_id,
                owner.organization_id,
                owner.owner_account_id,
                owner.model,
            )
        if not rows:
            raise RealtimeError("access_revoked", "Realtime access is unavailable")
        if any(value is not None for value in rows[0].values()):
            # HTTP does not yet participate in shared holds. Do not pretend a
            # WebSocket-only reservation protects budgets shared with HTTP.
            raise RealtimeError(
                "budget_profile_unsupported",
                "Budget-capped scopes are not yet supported for Realtime",
            )

    async def dispatch(
        self, operation_id: str, context: RealtimeChargeContext, *, expires_at: datetime
    ) -> None:
        async with billing_transaction(self.db, _deadline()) as tx:
            rows = await tx.query_raw(
                "INSERT INTO deltallm_realtime_billing_intents "
                "(operation_id,session_id,snapshot,expires_at) VALUES ($1,$2,$3::jsonb,$4::timestamptz) "
                "ON CONFLICT (operation_id) DO NOTHING RETURNING operation_id",
                operation_id,
                context.attribution.session_id,
                _encode(context.snapshot()),
                expires_at.isoformat(),
            )
            if not rows:
                # An ambiguous commit must never dispatch the same turn twice.
                raise BillingOperationUnavailable()
            capacity = await tx.query_raw(
                "UPDATE deltallm_telemetry_ingestion_capacity SET pending_count=pending_count+1 "
                "WHERE queue_name='realtime_billing' AND pending_count<$1 RETURNING pending_count",
                self.max_pending,
            )
            if not capacity:
                raise BillingOperationUnavailable()

    async def accept(
        self, operation_id: str, context: RealtimeChargeContext, receipt: RealtimeUsageReceipt
    ) -> None:
        facts = {
            "receipt_id": receipt.receipt_id,
            "operation": receipt.operation,
            "usage": asdict(receipt.usage) if receipt.usage is not None else None,
            "pending_reason": receipt.pending_reason,
        }
        encoded_facts = _encode(facts)
        async with billing_transaction(self.db, _deadline()) as tx:
            rows = await tx.query_raw(
                "SELECT state,receipt_facts,created_at::text AS started_at, "
                "CURRENT_TIMESTAMP::text AS completed_at FROM deltallm_realtime_billing_intents "
                "WHERE operation_id=$1 AND snapshot=$2::jsonb FOR UPDATE",
                operation_id,
                _encode(context.snapshot()),
            )
            if not rows:
                raise BillingOperationUnavailable()
            previous = rows[0]["receipt_facts"]
            if previous is not None:
                if isinstance(previous, str):
                    previous = json.loads(previous)
                if previous != json.loads(encoded_facts):
                    raise BillingOperationUnavailable()
                return
            # The journal owns per-turn dispatch time. Use the same database
            # clock for acceptance, and freeze both with the first receipt.
            payload = (
                None
                if receipt.pending_reason
                else context.spend_payload(
                    receipt,
                    operation_started_at=datetime.fromisoformat(rows[0]["started_at"]).astimezone(
                        UTC
                    ),
                    completed_at=datetime.fromisoformat(rows[0]["completed_at"]).astimezone(UTC),
                )
            )
            await tx.execute_raw(
                "UPDATE deltallm_realtime_billing_intents SET event_id=$2,receipt_facts=$3::jsonb, "
                "spend_payload=$4::jsonb,state=$5,pending_reason=$6,updated_at=NOW() WHERE operation_id=$1",
                operation_id,
                receipt.receipt_id,
                encoded_facts,
                _encode(payload) if payload is not None else None,
                "accepted" if payload is not None else "pending",
                receipt.pending_reason,
            )

    async def close(self, session_id: str) -> None:
        async with billing_transaction(self.db, _deadline()) as tx:
            await tx.execute_raw(
                "UPDATE deltallm_realtime_billing_intents SET state='pending', "
                "pending_reason='terminal_usage_missing',updated_at=NOW() "
                "WHERE session_id=$1 AND state='dispatched'",
                session_id,
            )


def _deadline() -> float:
    return asyncio.get_running_loop().time() + 0.25


def _encode(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"), allow_nan=False)
