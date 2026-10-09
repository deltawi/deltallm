"""One bounded native call per journal claim, commit, or failure transition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import uuid4

from src.billing.accounting.journal.accounting_journal_claims import JournalClaim, JournalFailure
from src.db.accounting.accounting_calls import (
    AccountingDatabaseCalls,
    AccountingQueryClient,
    AccountingProtocolUnavailable,
    outcome_may_be_ambiguous,
)
from src.db.accounting.permits.accounting_permit_results import invalid_result


class AccountingJournalWorkerRepository:
    def __init__(
        self, db: AccountingQueryClient, *, statement_budget_seconds: float = 0.25
    ) -> None:
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)

    async def claim(
        self,
        *,
        generation: int,
        worker_id: str,
        expires_at: float,
        limit: int = 128,
        lease_seconds: int = 30,
    ) -> JournalClaim:
        handle = JournalClaim(
            protocol_generation=generation, worker_id=worker_id, lease_token=uuid4(), sequences=()
        )
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("terminal claim entry limit is invalid")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
            raise ValueError("terminal claim lease is invalid")
        try:
            rows = await self._calls.call(
                "claim_terminal_journal",
                "SELECT * FROM deltallm_accounting_claim_terminal_journal($1,$2,$3::uuid,$4::integer,$5::integer)",
                generation,
                worker_id,
                str(handle.lease_token),
                lease_seconds,
                limit,
                expires_at=self._calls.attempt_deadline(expires_at),
            )
        except AccountingProtocolUnavailable as exc:
            if not outcome_may_be_ambiguous(exc):
                raise
            rows = await self._calls.call(
                "recover_terminal_claim",
                "SELECT j.sequence AS journal_sequence FROM deltallm_accounting_terminal_journal j "
                "WHERE j.generation=$1 AND j.status='processing' AND j.lease_owner=$2 "
                "AND j.lease_token=$3::uuid AND j.lease_expires_at>clock_timestamp() "
                "ORDER BY j.sequence LIMIT $4::integer",
                generation,
                worker_id,
                str(handle.lease_token),
                limit,
                expires_at=expires_at,
            )
            if not rows:
                raise exc
        return _claim_result(handle, rows, limit=limit)

    async def materialize(self, claim: JournalClaim, *, expires_at: float) -> int:
        claim = _validated_claim(claim)
        if not claim.sequences:
            return 0
        try:
            rows = await self._calls.call(
                "materialize_terminal_journal",
                "SELECT deltallm_accounting_materialize_terminal_journal($1,$2,$3::uuid,$4::bigint[]) AS count",
                claim.protocol_generation,
                claim.worker_id,
                str(claim.lease_token),
                list(claim.sequences),
                expires_at=self._calls.attempt_deadline(expires_at),
            )
        except AccountingProtocolUnavailable as exc:
            if not outcome_may_be_ambiguous(exc):
                raise
            rows = await self._calls.call(
                "recover_terminal_materialization",
                "SELECT count(*)::integer AS count FROM deltallm_accounting_terminal_journal j "
                "WHERE j.sequence=ANY($1::bigint[]) AND j.generation=$2 AND j.status='completed' "
                "AND j.materialized_event_sequence IS NOT NULL",
                list(claim.sequences),
                claim.protocol_generation,
                expires_at=expires_at,
            )
            if _count(rows, limit=len(claim.sequences)) != len(claim.sequences):
                raise exc
        return _count(rows, limit=len(claim.sequences))

    async def fail(self, claim: JournalClaim, failure: JournalFailure, *, expires_at: float) -> int:
        claim = _validated_claim(claim)
        failure = JournalFailure(failure)
        if not claim.sequences:
            return 0
        rows = await self._calls.call(
            "fail_terminal_journal",
            "SELECT deltallm_accounting_fail_terminal_journal($1,$2,$3::uuid,$4::bigint[],$5) AS count",
            claim.protocol_generation,
            claim.worker_id,
            str(claim.lease_token),
            list(claim.sequences),
            failure.value,
            expires_at=expires_at,
        )
        return _count(rows, limit=len(claim.sequences))


def _validated_claim(claim: JournalClaim) -> JournalClaim:
    # Read raw scalars: JSON serialization can turn a copied boolean into 1.
    return JournalClaim(
        protocol_generation=claim.protocol_generation,
        worker_id=claim.worker_id,
        lease_token=claim.lease_token,
        sequences=claim.sequences,
    )


def _claim_result(
    handle: JournalClaim, rows: Sequence[Mapping[str, object]], *, limit: int
) -> JournalClaim:
    keys = tuple(row.get("journal_sequence") for row in rows)
    if len(keys) > limit or any(type(key) is not int for key in keys):
        raise invalid_result()
    try:
        return JournalClaim(**{**handle.model_dump(), "sequences": keys})
    except ValueError:
        raise invalid_result() from None


def _count(rows: Sequence[Mapping[str, object]], *, limit: int) -> int:
    if (
        len(rows) != 1
        or type(rows[0].get("count")) is not int
        or not 0 <= rows[0]["count"] <= limit
    ):
        raise invalid_result()
    return rows[0]["count"]
