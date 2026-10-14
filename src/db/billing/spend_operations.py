"""Reserved spend slots and owner-fenced receipt replacement in the existing outbox."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import TYPE_CHECKING

from src.billing.spend.spend_operations import (
    ADMISSION_BUDGET_SECONDS,
    OperationHandle,
    SpendPersistenceUnavailable,
)
from src.billing.charges.operation_reservation import BillingOperationUnavailable
from src.db.billing.billing_transaction import DB_BUDGET_SECONDS
from src.db.runtime.telemetry_acceptance import AcceptanceFailure, classify_acceptance_failure

if TYPE_CHECKING:
    from prisma import Prisma

logger = logging.getLogger(__name__)

_VERDICT_MARGIN_SECONDS = 0.05
_RELEASE_RETRY_DELAYS_SECONDS = (0.0, 0.05, 0.25)
# Strong references keep detached releases alive until they finish.
_RELEASES: set[asyncio.Task] = set()


class SpendOperationRepository:
    def __init__(self, db: Prisma) -> None:
        self.db = db

    async def verify_schema(self) -> None:
        await self.db.query_raw(
            "SELECT operation_owner,operation_state,operation_intent,operation_expires_at "
            "FROM deltallm_spend_ingestion_outbox LIMIT 0"
        )

    async def begin(
        self, handle: OperationHandle, *, capacity: int, max_attempts: int, expires_at: float
    ) -> int:
        """Reserve or append one dispatch in one self-committing statement.

        The SQL function takes the global admission lock and reads capacity after
        it, so concurrent replicas cannot over-admit. The lock ends with the
        statement's own commit; no client round trip runs while it is held.
        An exact repeated dispatch is rejected, including ambiguous prior commits.
        The durable event ID prevents redispatch after transient outbox retention.
        """
        intent = handle.intent.model_dump_json()
        if len(intent.encode()) > 65_536:
            raise SpendPersistenceUnavailable()
        previous = handle.intent.model_dump(mode="json")
        previous["attempts"] = previous["attempts"][:-1]
        attempt_count = len(handle.intent.attempts)
        now = asyncio.get_running_loop().time()
        if not math.isfinite(expires_at) or expires_at <= now:
            raise BillingOperationUnavailable()
        budget = min(expires_at - now, ADMISSION_BUDGET_SECONDS)
        # The lock wait ends first so the database's verdict normally arrives
        # before the caller's deadline.
        lock_timeout_ms = max(1, int((budget - _VERDICT_MARGIN_SECONDS) * 1000))
        admission = asyncio.ensure_future(
            self.db.query_raw(
                _BEGIN_OPERATION,
                str(handle.event_id),
                str(handle.owner_token),
                intent,
                handle.expires_at.isoformat(),
                max_attempts,
                capacity,
                json.dumps(previous),
                attempt_count,
                lock_timeout_ms,
            )
        )
        try:
            async with asyncio.timeout(budget):
                rows = await asyncio.shield(admission)
        except (Exception, asyncio.CancelledError) as exc:
            # The statement may still commit after a timeout or cancellation.
            # No provider call has started, so a first attempt frees its slot.
            if attempt_count == 1:
                self._release_unused(admission, handle, intent)
            if isinstance(exc, asyncio.CancelledError):
                raise
            reason = classify_acceptance_failure(exc)
            if reason != AcceptanceFailure.UNKNOWN:
                logger.warning(
                    "spend admission failed reason=%s budget_ms=%d",
                    reason.value,
                    round(budget * 1000),
                )
            raise BillingOperationUnavailable() from None
        if not rows or not rows[0]["accepted"]:
            raise BillingOperationUnavailable()
        return int(rows[0]["pending_count"])

    def _release_unused(
        self, admission: asyncio.Future, handle: OperationHandle, intent: str
    ) -> None:
        task = asyncio.get_running_loop().create_task(
            self._release_after(admission, handle, intent)
        )
        _RELEASES.add(task)
        task.add_done_callback(_RELEASES.discard)

    async def _release_after(
        self, admission: asyncio.Future, handle: OperationHandle, intent: str
    ) -> None:
        try:
            rows = await admission
        except Exception:
            pass  # Outcome unknown; the release only matches this exact reservation.
        else:
            if not rows or not rows[0]["accepted"]:
                return
        for delay in _RELEASE_RETRY_DELAYS_SECONDS:
            await asyncio.sleep(delay)
            try:
                await self.db.query_raw(
                    "SELECT deltallm_release_spend_operation($1, $2, $3::jsonb) AS released",
                    str(handle.event_id),
                    str(handle.owner_token),
                    intent,
                )
                return
            except Exception:
                continue
        # Expiry recovery still reclaims the slot as an unknown operation.
        logger.warning("spend admission release failed; expiry recovery will reclaim the slot")

    async def accept(
        self,
        *,
        event_id: str,
        owner_token: str,
        payload: dict[str, object],
        expires_at: float,
    ) -> None:
        encoded = json.dumps(payload, default=str, allow_nan=False, sort_keys=True)
        if len(encoded.encode()) > 262_144:
            raise SpendPersistenceUnavailable()
        rows = await self._settlement_statement(
            "UPDATE deltallm_spend_ingestion_outbox SET "
            "payload_json=$3::jsonb,operation_state='accepted',"
            "status=CASE WHEN operation_state='accepted' THEN status ELSE 'queued' END,"
            "blocked_at=CASE WHEN operation_state='accepted' THEN blocked_at ELSE NULL END,"
            "last_error=CASE WHEN operation_state='accepted' THEN last_error ELSE NULL END,"
            "updated_at=NOW() "
            "WHERE event_id=$1 AND operation_owner=$2 "
            "AND (operation_state IN ('dispatched','unknown') OR "
            "(operation_state='accepted' AND payload_json=$3::jsonb)) "
            "AND operation_intent->'principal' = $4::jsonb "
            "AND operation_intent->>'model'=$5 AND operation_intent->>'call_type'=$6 "
            "RETURNING event_id",
            event_id,
            owner_token,
            encoded,
            json.dumps(
                {
                    name: payload.get(name)
                    for name in (
                        "api_key",
                        "user_id",
                        "team_id",
                        "organization_id",
                        "owner_account_id",
                    )
                }
            ),
            payload.get("model"),
            payload.get("call_type"),
            expires_at=expires_at,
        )
        if not rows:
            raise BillingOperationUnavailable()

    async def mark_unknown(self, *, event_id: str, owner_token: str, expires_at: float) -> bool:
        rows = await self._settlement_statement(
            "UPDATE deltallm_spend_ingestion_outbox SET operation_state='unknown',"
            "last_error='operation_outcome_unknown',updated_at=NOW() "
            "WHERE event_id=$1 AND operation_owner=$2 AND operation_state='dispatched' RETURNING event_id",
            event_id,
            owner_token,
            expires_at=expires_at,
        )
        return bool(rows)

    async def _settlement_statement(
        self, query: str, *parameters: object, expires_at: float
    ) -> list[dict[str, object]]:
        # These fenced updates each need one implicit PostgreSQL transaction.
        # Keep admission's multi-statement transaction separate. The allocated
        # client owns native work after caller cancellation/lost acknowledgement;
        # a late commit is recovered by event ID, never by repeating provider work.
        remaining = expires_at - asyncio.get_running_loop().time()
        if not math.isfinite(remaining) or remaining <= 0:
            raise BillingOperationUnavailable()
        try:
            async with asyncio.timeout(min(remaining, DB_BUDGET_SECONDS)):
                return await self.db.query_raw(query, *parameters)
        except Exception:
            raise BillingOperationUnavailable() from None

    async def recover_expired(self, *, limit: int = 100) -> int:
        rows = await self.db.query_raw(
            "WITH candidates AS MATERIALIZED (SELECT ctid FROM deltallm_spend_ingestion_outbox "
            "WHERE operation_state='dispatched' AND operation_expires_at<=NOW() "
            "ORDER BY operation_expires_at,event_id FOR UPDATE SKIP LOCKED LIMIT $1) "
            "UPDATE deltallm_spend_ingestion_outbox o SET operation_state='unknown',"
            "last_error='operation_outcome_unknown',updated_at=NOW() "
            "WHERE o.ctid=ANY(ARRAY(SELECT ctid FROM candidates)) RETURNING o.event_id",
            max(1, min(limit, 100)),
        )
        return len(rows)

    async def unknown_count(self) -> int:
        # The existing blocked-record partial index bounds work by queue capacity,
        # not retained completed-event history. Background observation only.
        rows = await self.db.query_raw(
            "SELECT count(*)::bigint AS count FROM deltallm_spend_ingestion_outbox "
            "WHERE status='blocked' AND operation_state='unknown'"
        )
        return int(rows[0]["count"])


_BEGIN_OPERATION = (
    "SELECT accepted, pending_count FROM deltallm_begin_spend_operation("
    "$1, $2, $3::jsonb, $4::timestamptz, $5::int, $6::bigint, $7::jsonb, $8::int, $9::int)"
)
