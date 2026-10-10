"""Privileged maintenance reconciliation, atomic with its required operator audit."""

from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING
from uuid import uuid5, NAMESPACE_URL

from src.billing.money import money_string
from src.billing.spend_operations import SpendOperationIntent, SpendPersistenceUnavailable
from src.billing.spend_preparation import prepare_spend_event
from src.billing.spend_reconciliation import SpendOperationResolution
from src.db.billing_operations import BillingOperationRepository
from src.db.repositories import AuditEventRecord, AuditRepository


if TYPE_CHECKING:
    from prisma import Prisma


class SpendReconciliationRepository:
    def __init__(self, db: "Prisma") -> None:
        self.transactions = BillingOperationRepository(db)

    async def reconcile(self, resolution: SpendOperationResolution, *, expires_at: float) -> None:
        async with self.transactions._transaction(expires_at) as tx:
            rows = await tx.query_raw(
                "SELECT operation_intent,operation_state,operation_resolution FROM "
                "deltallm_spend_ingestion_outbox WHERE event_id=$1 AND "
                "operation_intent->'principal'->>'organization_id' IS NOT DISTINCT FROM $2 "
                "FOR UPDATE",
                str(resolution.event_id),
                resolution.organization_id,
            )
            if not rows:
                raise SpendPersistenceUnavailable()
            row = rows[0]
            if row["operation_resolution"] == resolution.model_dump(mode="json"):
                return
            if row["operation_state"] != "unknown":
                raise SpendPersistenceUnavailable()
            intent = SpendOperationIntent.model_validate(row["operation_intent"])
            now = datetime.now(UTC)
            payload = {
                **intent.principal.model_dump(),
                "request_id": "",
                "model": intent.model,
                "call_type": intent.call_type,
                "usage": resolution.usage,
                "cost_exact": money_string(resolution.cost_exact),
                "provider_cost_exact": money_string(resolution.provider_cost_exact)
                if resolution.provider_cost_exact is not None
                else None,
                "spend_event_version": 2,
                "start_time": intent.started_at.isoformat(),
                "end_time": now.isoformat(),
                "metadata": {
                    "operation_intent": intent.model_dump(mode="json"),
                    "operation_outcome": resolution.outcome,
                    "reconciliation": resolution.model_dump(mode="json"),
                },
            }
            prepare_spend_event(
                event_id=str(resolution.event_id),
                event_type="spend",
                payload={**payload, "start_time": intent.started_at, "end_time": now},
            )

            await tx.execute_raw(
                "UPDATE deltallm_spend_ingestion_outbox SET operation_state='accepted',"
                "operation_resolution=$2::jsonb,payload_json=$3::jsonb,status='queued',"
                "blocked_at=NULL,last_error=NULL,updated_at=NOW() WHERE event_id=$1",
                str(resolution.event_id),
                resolution.model_dump_json(),
                json.dumps(payload),
            )
            await AuditRepository(tx).create_event(
                AuditEventRecord(
                    event_id=str(
                        uuid5(NAMESPACE_URL, f"deltallm:spend-resolution:{resolution.event_id}")
                    ),
                    action="BILLING_OPERATION_RECONCILED",
                    occurred_at=now,
                    organization_id=resolution.organization_id,
                    actor_type="operator",
                    actor_id=resolution.actor_id,
                    resource_type="spend_operation",
                    resource_id=str(resolution.event_id),
                    status="success",
                    metadata={
                        "outcome": resolution.outcome,
                        "evidence_reference": resolution.evidence_reference,
                    },
                )
            )
