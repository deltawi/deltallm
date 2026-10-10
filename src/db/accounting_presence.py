"""Use fixed slot probes and fenced updates for projection-role health."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from src.billing.accounting_presence import ProjectionLease, ProjectionPresence
from src.db.accounting_calls import AccountingDatabaseCalls, AccountingQueryClient
from src.db.accounting_permit_results import invalid_result


_PRESENCE = "deltallm_accounting_projection_presence"
_SNAPSHOT = (
    "WITH protocol AS MATERIALIZED (SELECT generation FROM deltallm_accounting_protocols "
    "WHERE protocol_name='primary' AND generation=$1 AND writer_version=2), "
    "slots AS MATERIALIZED (SELECT lease.owner_token,lease.ready,lease.expires_at "
    "FROM generate_series(0,63) key(slot) LEFT JOIN LATERAL "
    "(SELECT owner_token,ready,expires_at FROM deltallm_accounting_projection_presence "
    "WHERE protocol_name='primary' AND generation=$1 AND slot=key.slot OFFSET 0) lease ON TRUE) "
    "SELECT protocol.generation, count(slots.owner_token)::integer AS present_slots, "
    "count(*) FILTER (WHERE slots.ready AND slots.expires_at>statement_timestamp())::integer "
    "AS ready_slots FROM protocol CROSS JOIN slots GROUP BY protocol.generation"
)


class AccountingPresenceRepository:
    def __init__(
        self, db: AccountingQueryClient, *, statement_budget_seconds: float = 0.25
    ) -> None:
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)

    async def initialize(self, *, generation: int, expires_at: float) -> None:
        _generation(generation)
        rows = await self._calls.call(
            "presence_initialize",
            "INSERT INTO "
            + _PRESENCE
            + "(protocol_name,generation,slot) SELECT 'primary',$1,key FROM generate_series(0,63) key "
            "ON CONFLICT DO NOTHING RETURNING slot",
            generation,
            expires_at=expires_at,
        )
        slots = [row.get("slot") for row in rows]
        if len(slots) > 64 or any(type(slot) is not int or not 0 <= slot <= 63 for slot in slots):
            raise invalid_result()
        if len(set(slots)) != len(slots):
            raise invalid_result()

    async def acquire(
        self, *, generation: int, owner_token: UUID, lease_seconds: int, expires_at: float
    ) -> ProjectionLease | None:
        _generation(generation)
        _lease_seconds(lease_seconds)
        if type(owner_token) is not UUID:
            raise ValueError("projection presence owner is invalid")
        rows = await self._calls.call(
            "presence_acquire",
            "WITH candidate AS MATERIALIZED ("
            "SELECT lease.slot FROM generate_series(0,63) key(slot) CROSS JOIN LATERAL "
            "(SELECT p.slot FROM deltallm_accounting_projection_presence p "
            "WHERE p.protocol_name='primary' AND p.generation=$1 AND p.slot=key.slot "
            "AND p.expires_at<=statement_timestamp() FOR UPDATE SKIP LOCKED OFFSET 0) lease "
            "ORDER BY lease.slot LIMIT 1) UPDATE deltallm_accounting_projection_presence p SET "
            "owner_token=$2::uuid,expires_at=statement_timestamp()+make_interval(secs=>$3),ready=FALSE "
            "FROM candidate WHERE p.protocol_name='primary' AND p.generation=$1 "
            "AND p.slot=candidate.slot RETURNING p.generation,p.slot,p.owner_token",
            generation,
            str(owner_token),
            lease_seconds,
            expires_at=expires_at,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise invalid_result()
        try:
            row = rows[0]
            lease = ProjectionLease(
                generation=row["generation"],
                slot=row["slot"],
                owner_token=UUID(str(row["owner_token"])),
            )
        except (KeyError, TypeError, ValueError):
            raise invalid_result() from None
        if lease.generation != generation or lease.owner_token != owner_token:
            raise invalid_result()
        return lease

    async def publish(
        self, lease: ProjectionLease, *, ready: bool, lease_seconds: int, expires_at: float
    ) -> bool:
        lease = _validated_lease(lease)
        _lease_seconds(lease_seconds)
        if type(ready) is not bool:
            raise ValueError("projection presence readiness is invalid")
        rows = await self._calls.call(
            "presence_publish",
            "UPDATE "
            + _PRESENCE
            + " SET expires_at=statement_timestamp()+make_interval(secs=>$4),ready=$5 "
            "WHERE protocol_name='primary' AND generation=$1 AND slot=$2 AND owner_token=$3::uuid "
            "AND expires_at>statement_timestamp() RETURNING slot",
            lease.generation,
            lease.slot,
            str(lease.owner_token),
            lease_seconds,
            ready,
            expires_at=expires_at,
        )
        return _accepted(rows, lease)

    async def release(self, lease: ProjectionLease, *, expires_at: float) -> bool:
        lease = _validated_lease(lease)
        rows = await self._calls.call(
            "presence_release",
            "UPDATE "
            + _PRESENCE
            + " SET expires_at=statement_timestamp(),ready=FALSE WHERE protocol_name='primary' "
            "AND generation=$1 AND slot=$2 AND owner_token=$3::uuid RETURNING slot",
            lease.generation,
            lease.slot,
            str(lease.owner_token),
            expires_at=expires_at,
        )
        return _accepted(rows, lease)

    async def snapshot(self, *, generation: int, expires_at: float) -> ProjectionPresence:
        _generation(generation)
        rows = await self._calls.call(
            "presence_snapshot", _SNAPSHOT, generation, expires_at=expires_at
        )
        if len(rows) != 1:
            raise invalid_result()
        try:
            value = ProjectionPresence.model_validate(rows[0])
        except ValueError:
            raise invalid_result() from None
        if value.generation != generation:
            raise invalid_result()
        return value


def _generation(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError("projection presence generation is invalid")


def _lease_seconds(value: int) -> None:
    if type(value) is not int or not 5 <= value <= 30:
        raise ValueError("projection presence lease is invalid")


def _validated_lease(value: ProjectionLease) -> ProjectionLease:
    if type(value) is not ProjectionLease:
        raise ValueError("projection presence lease is invalid")
    return ProjectionLease(
        generation=value.generation, slot=value.slot, owner_token=value.owner_token
    )


def _accepted(rows: Sequence[Mapping[str, object]], lease: ProjectionLease) -> bool:
    if len(rows) > 1 or (
        rows and (type(rows[0].get("slot")) is not int or rows[0]["slot"] != lease.slot)
    ):
        raise invalid_result()
    return bool(rows)
