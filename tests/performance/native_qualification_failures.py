"""Keep failed-stage results and bounded, read-only accounting evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from prisma import Prisma
from pydantic import BaseModel, ConfigDict, Field

_UNSETTLED_SQL = """
WITH selected AS MATERIALIZED (
 SELECT operation_id,accounting_state,final_event_sequence
 FROM deltallm_billing_operations
 WHERE accounting_protocol='primary' AND accounting_generation=$1
  AND accounting_state IN ('reserved','provisional')
 ORDER BY accounting_state,expires_at,operation_id LIMIT 65
)
SELECT op.operation_id,op.accounting_state,
 COALESCE(held.reserved,0)::text AS max_scope_reserved_exact,
 COALESCE(held.provisional,0)::text AS max_scope_provisional_exact,
 journal.outcome AS journal_outcome,journal.status AS journal_status,
 CASE COALESCE(event.payload_json->>'uncertainty_reason',
               payload.finalization_payload::jsonb->>'uncertainty_reason')
  WHEN 'ServiceUnavailableError' THEN 'service_unavailable'
  WHEN 'service_unavailable' THEN 'service_unavailable'
  WHEN 'ProviderUnavailableError' THEN 'provider_unavailable'
  WHEN 'TimeoutError' THEN 'timeout'
  WHEN 'timeout_error' THEN 'timeout'
  WHEN 'CancelledError' THEN 'cancelled'
  WHEN 'reservation_expired' THEN 'reservation_expired'
  WHEN 'provider_outcome_unknown' THEN 'provider_outcome_unknown'
  ELSE 'unknown' END AS uncertainty_class
FROM selected op
LEFT JOIN LATERAL (
 SELECT max(allowance_exact-committed_exact-provisional_exact-released_exact) AS reserved,
        max(provisional_exact) AS provisional
 FROM deltallm_accounting_reservations WHERE operation_id=op.operation_id
) held ON TRUE
LEFT JOIN deltallm_accounting_terminal_journal journal ON journal.operation_id=op.operation_id
LEFT JOIN deltallm_accounting_terminal_payloads payload ON payload.journal_sequence=journal.sequence
LEFT JOIN deltallm_accounting_events event ON event.sequence=op.final_event_sequence
ORDER BY op.accounting_state,op.operation_id
"""


class UnsettledOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=57)

    operation_id: UUID
    accounting_state: Literal["reserved", "provisional"]
    max_scope_reserved_exact: str = Field(pattern=r"^[0-9]{1,38}(\.[0-9]{1,18})?$")
    max_scope_provisional_exact: str = Field(pattern=r"^[0-9]{1,38}(\.[0-9]{1,18})?$")
    journal_outcome: Literal["completed", "uncertain", "not_dispatched"] | None
    journal_status: Literal["pending", "processing", "completed", "failed"] | None
    uncertainty_class: Literal[
        "service_unavailable",
        "provider_unavailable",
        "timeout",
        "cancelled",
        "reservation_expired",
        "provider_outcome_unknown",
        "unknown",
    ]


async def capture_unsettled_operations(db: Prisma, *, generation: int = 1) -> dict[str, object]:
    """Capture at most 64 scalar rows after arrivals; never change money."""
    try:
        async with asyncio.timeout(5):
            async with db.tx(max_wait=timedelta(seconds=1), timeout=timedelta(seconds=4)) as tx:
                await tx.execute_raw("SET TRANSACTION READ ONLY")
                await tx.execute_raw("SET LOCAL statement_timeout = '2000ms'")
                await tx.execute_raw("SET LOCAL lock_timeout = '250ms'")
                rows = await tx.query_raw(_UNSETTLED_SQL, generation)
        if len(rows) > 65:
            raise ValueError("Unsettled capture exceeded its row bound")
        operations = [UnsettledOperation.model_validate(row) for row in rows]
    except Exception:
        return {"available": False, "error": "unsettled_capture_unavailable", "operations": None}
    return {
        "available": True,
        "truncated": len(operations) > 64,
        "operations": [operation.model_dump(mode="json") for operation in operations[:64]],
        "window": "after the drain deadline; read-only; not arrival-window work",
    }


class QualificationStageStopped(RuntimeError):
    def __init__(self, message: str, report: dict[str, object]) -> None:
        super().__init__(message)
        self.report = report


async def record_stage_result(
    run: Callable[[], Awaitable[dict[str, object]]],
    *,
    output: Path,
    proof: dict[str, object],
    results: list[dict[str, object]],
) -> dict[str, object]:
    """Publish a stopped stage before its unchanged stop condition propagates."""
    failure = None
    try:
        result = await run()
    except QualificationStageStopped as error:
        result, failure = error.report, error
    results.append(result)
    (output / "results.json").write_text(
        json.dumps({"generator_proof": proof, "runs": results}, indent=2) + "\n"
    )
    if failure is not None:
        raise failure
    return result
