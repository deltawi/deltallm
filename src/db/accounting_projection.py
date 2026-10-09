"""Fenced partition claims for accounting compatibility projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from prisma import Prisma


@dataclass(frozen=True, slots=True)
class AccountingProjectionClaim:
    projection_name: str
    protocol_generation: int
    accounting_partition: int
    worker_id: str
    lease_token: str
    last_sequence: int


@dataclass(frozen=True, slots=True)
class AccountingProjectionEvent:
    sequence: int
    event_id: str
    operation_id: str
    accounting_partition: int
    outcome: str
    payload: dict[str, object]
    audit_envelope: dict[str, object]
    occurred_at: datetime


class AccountingProjectionRepository:
    def __init__(self, db: Prisma) -> None:
        self.db = db

    async def initialize(self, *, projection_name: str, generation: int) -> None:
        await self.db.execute_raw(
            "INSERT INTO deltallm_accounting_projection_checkpoints "
            "(projection_name,protocol_name,generation,accounting_partition) "
            "SELECT $1,'primary',$2,partition_id FROM deltallm_accounting_partitions "
            "WHERE protocol_name='primary' AND generation=$2 ON CONFLICT DO NOTHING",
            projection_name,
            generation,
        )

    async def reconcile_expired(self, *, generation: int, limit: int) -> int:
        bounded = max(1, min(int(limit), 256))
        # Each function commits its lock domain before the next begins. In
        # particular, expiry owns operation/grant/partition locks while grant
        # closure owns grant/window locks; combining them in one statement
        # would retain both domains and invert refill's window/partition order.
        grant_expired = await self.db.query_raw(
            "SELECT deltallm_accounting_reconcile_expired_grants($1,$2::integer) AS count",
            generation,
            bounded,
        )
        legacy_expired = await self.db.query_raw(
            "SELECT deltallm_accounting_reconcile_expired($1,$2::integer) AS count",
            generation,
            bounded,
        )
        await self.db.query_raw(
            "SELECT deltallm_accounting_reconcile_grants($1,$2::integer) AS count",
            generation,
            bounded,
        )
        return sum(int(rows[0]["count"]) if rows else 0 for rows in (grant_expired, legacy_expired))

    async def roll_budget_windows(self, *, generation: int, limit: int) -> int:
        rows = await self.db.query_raw(
            "SELECT deltallm_accounting_roll_windows($1,$2::integer) AS rolled_count",
            generation,
            max(1, min(int(limit), 256)),
        )
        return int(rows[0]["rolled_count"]) if rows else 0

    async def backlog(self, *, projection_name: str, generation: int) -> tuple[int, float]:
        rows = await self.db.query_raw(
            "SELECT COUNT(e.sequence)::bigint AS pending_count,"
            "COALESCE(EXTRACT(EPOCH FROM (NOW()-MIN(e.occurred_at))),0)::float8 AS oldest_age "
            "FROM deltallm_accounting_projection_checkpoints c "
            "JOIN deltallm_accounting_events e ON e.protocol_name=c.protocol_name "
            "AND e.generation=c.generation AND e.accounting_partition=c.accounting_partition "
            "AND e.sequence>c.last_sequence AND e.event_type IN ('finalized','reconciled') "
            "WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=$2",
            projection_name,
            generation,
        )
        if not rows:
            return 0, 0.0
        return int(rows[0].get("pending_count") or 0), float(rows[0].get("oldest_age") or 0.0)

    async def claim_next(
        self,
        *,
        projection_name: str,
        generation: int,
        worker_id: str,
        lease_seconds: int,
    ) -> AccountingProjectionClaim | None:
        token = str(uuid4())
        rows = await self.db.query_raw(
            "WITH candidate AS MATERIALIZED ("
            "SELECT checkpoint.accounting_partition "
            "FROM deltallm_accounting_projection_checkpoints checkpoint "
            "WHERE checkpoint.projection_name=$1 AND checkpoint.protocol_name='primary' "
            "AND checkpoint.generation=$2 "
            "AND (checkpoint.lease_expires_at IS NULL OR checkpoint.lease_expires_at<=NOW()) "
            "AND EXISTS (SELECT 1 FROM deltallm_accounting_events event "
            "WHERE event.protocol_name=checkpoint.protocol_name "
            "AND event.generation=checkpoint.generation "
            "AND event.accounting_partition=checkpoint.accounting_partition "
            "AND event.sequence>checkpoint.last_sequence "
            "AND event.event_type IN ('finalized','reconciled')) "
            "ORDER BY checkpoint.updated_at,checkpoint.accounting_partition "
            "FOR UPDATE SKIP LOCKED LIMIT 1"
            ") UPDATE deltallm_accounting_projection_checkpoints c SET "
            "lease_owner=$3,lease_token=$4,lease_expires_at=NOW()+make_interval(secs=>$5),"
            "last_error_code=NULL,updated_at=NOW() FROM candidate "
            "WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=$2 "
            "AND c.accounting_partition=candidate.accounting_partition "
            "RETURNING c.accounting_partition,c.last_sequence",
            projection_name,
            generation,
            worker_id,
            token,
            max(1, min(int(lease_seconds), 300)),
        )
        if not rows:
            return None
        return AccountingProjectionClaim(
            projection_name=projection_name,
            protocol_generation=generation,
            accounting_partition=int(rows[0]["accounting_partition"]),
            worker_id=worker_id,
            lease_token=token,
            last_sequence=int(rows[0]["last_sequence"]),
        )

    async def claim_many(
        self,
        *,
        projection_name: str,
        generation: int,
        worker_id: str,
        lease_seconds: int,
        limit: int,
    ) -> list[AccountingProjectionClaim]:
        """Claim several independent partitions with one fenced database call."""

        token = str(uuid4())
        rows = await self.db.query_raw(
            "WITH candidates AS MATERIALIZED ("
            "SELECT checkpoint.accounting_partition "
            "FROM deltallm_accounting_projection_checkpoints checkpoint "
            "WHERE checkpoint.projection_name=$1 AND checkpoint.protocol_name='primary' "
            "AND checkpoint.generation=$2 "
            "AND (checkpoint.lease_expires_at IS NULL OR checkpoint.lease_expires_at<=NOW()) "
            "AND EXISTS (SELECT 1 FROM deltallm_accounting_events event "
            "WHERE event.protocol_name=checkpoint.protocol_name "
            "AND event.generation=checkpoint.generation "
            "AND event.accounting_partition=checkpoint.accounting_partition "
            "AND event.sequence>checkpoint.last_sequence "
            "AND event.event_type IN ('finalized','reconciled')) "
            "ORDER BY checkpoint.updated_at,checkpoint.accounting_partition "
            "FOR UPDATE SKIP LOCKED LIMIT $6"
            ") UPDATE deltallm_accounting_projection_checkpoints c SET "
            "lease_owner=$3,lease_token=$4,lease_expires_at=NOW()+make_interval(secs=>$5),"
            "last_error_code=NULL,updated_at=NOW() FROM candidates "
            "WHERE c.projection_name=$1 AND c.protocol_name='primary' AND c.generation=$2 "
            "AND c.accounting_partition=candidates.accounting_partition "
            "RETURNING c.accounting_partition,c.last_sequence",
            projection_name,
            generation,
            worker_id,
            token,
            max(1, min(int(lease_seconds), 300)),
            max(1, min(int(limit), 64)),
        )
        return [
            AccountingProjectionClaim(
                projection_name=projection_name,
                protocol_generation=generation,
                accounting_partition=int(row["accounting_partition"]),
                worker_id=worker_id,
                lease_token=token,
                last_sequence=int(row["last_sequence"]),
            )
            for row in rows
        ]

    async def read_batch(
        self, claim: AccountingProjectionClaim, *, limit: int
    ) -> list[AccountingProjectionEvent]:
        rows = await self.db.query_raw(
            "SELECT e.sequence,e.event_id,e.operation_id,e.accounting_partition,e.outcome,"
            "e.payload_json,e.audit_envelope_json,e.occurred_at "
            "FROM deltallm_accounting_events e "
            "JOIN deltallm_accounting_projection_checkpoints c ON "
            "c.projection_name=$1 AND c.protocol_name=e.protocol_name "
            "AND c.generation=e.generation AND c.accounting_partition=e.accounting_partition "
            "WHERE e.protocol_name='primary' AND e.generation=$2 "
            "AND e.accounting_partition=$3 AND e.event_type IN ('finalized','reconciled') "
            "AND e.sequence>c.last_sequence AND c.lease_owner=$4 AND c.lease_token=$5 "
            "AND c.lease_expires_at>NOW() ORDER BY e.sequence LIMIT $6",
            claim.projection_name,
            claim.protocol_generation,
            claim.accounting_partition,
            claim.worker_id,
            claim.lease_token,
            max(1, min(int(limit), 256)),
        )
        return [
            AccountingProjectionEvent(
                sequence=int(row["sequence"]),
                event_id=str(row["event_id"]),
                operation_id=str(row["operation_id"]),
                accounting_partition=int(row["accounting_partition"]),
                outcome=str(row["outcome"]),
                payload=dict(row.get("payload_json") or {}),
                audit_envelope=dict(row.get("audit_envelope_json") or {}),
                occurred_at=_required_datetime(row.get("occurred_at")),
            )
            for row in rows
        ]

    async def complete(
        self, claim: AccountingProjectionClaim, *, last_sequence: int | None = None
    ) -> bool:
        rows = await self.db.query_raw(
            "UPDATE deltallm_accounting_projection_checkpoints SET "
            "last_sequence=CASE WHEN $6::bigint IS NULL THEN last_sequence "
            "ELSE GREATEST(last_sequence,$6) END,lease_owner=NULL,lease_token=NULL,"
            "lease_expires_at=NULL,last_error_code=NULL,updated_at=NOW() "
            "WHERE projection_name=$1 AND protocol_name='primary' AND generation=$2 "
            "AND accounting_partition=$3 AND lease_owner=$4 AND lease_token=$5 "
            "RETURNING accounting_partition",
            claim.projection_name,
            claim.protocol_generation,
            claim.accounting_partition,
            claim.worker_id,
            claim.lease_token,
            last_sequence,
        )
        return bool(rows)

    async def fail(self, claim: AccountingProjectionClaim, *, error_code: str) -> bool:
        rows = await self.db.query_raw(
            "UPDATE deltallm_accounting_projection_checkpoints SET "
            "lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,last_error_code=$6,"
            "updated_at=NOW() WHERE projection_name=$1 AND protocol_name='primary' "
            "AND generation=$2 AND accounting_partition=$3 AND lease_owner=$4 "
            "AND lease_token=$5 RETURNING accounting_partition",
            claim.projection_name,
            claim.protocol_generation,
            claim.accounting_partition,
            claim.worker_id,
            claim.lease_token,
            error_code[:128],
        )
        return bool(rows)


def _required_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("accounting projection timestamp is invalid")
    if parsed.tzinfo is None:
        raise ValueError("accounting projection timestamp must include a timezone")
    return parsed.astimezone(UTC)
