"""One native statement owns each bounded reporting claim and atomic commit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID, uuid4

from src.billing.accounting_read_model_claims import READ_MODEL_PROJECTION, ReadModelClaim
from src.billing.accounting_read_model_health import ReadModelProgress
from src.db.accounting_calls import (
    AccountingDatabaseCalls,
    AccountingProtocolUnavailable,
    AccountingQueryClient,
    outcome_may_be_ambiguous,
)
from src.db.accounting_permit_results import invalid_result
from src.db.accounting_read_model_queries import (
    CLAIM,
    INITIALIZE,
    PROGRESS,
    RECOVER_CLAIM,
    VERIFY_CELLS,
)


class AccountingReadModelRepository:
    def __init__(
        self, client: AccountingQueryClient, *, statement_budget_seconds: float = 0.25
    ) -> None:
        self._calls = AccountingDatabaseCalls(
            client, statement_budget_seconds=statement_budget_seconds
        )

    async def initialize(self, *, generation: int, expires_at: float) -> None:
        _generation(generation)
        rows = await self._calls.call(
            "initialize_read_model",
            INITIALIZE,
            READ_MODEL_PROJECTION,
            generation,
            expires_at=expires_at,
        )
        if _initialized(rows, generation):
            return
        # A concurrent insert can commit after this statement's snapshot. Check
        # its fixed cells once with a new snapshot, under the same deadline.
        rows = await self._calls.call(
            "verify_read_model_cells",
            VERIFY_CELLS,
            READ_MODEL_PROJECTION,
            generation,
            expires_at=expires_at,
        )
        if not _initialized(rows, generation):
            raise invalid_result()

    async def claim(
        self,
        *,
        generation: int,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        expires_at: float,
    ) -> ReadModelClaim | None:
        _generation(generation)
        if type(worker_id) is not str or not 1 <= len(worker_id) <= 256:
            raise ValueError("read-model worker identity is invalid")
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("read-model page limit is invalid")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
            raise ValueError("read-model lease is invalid")
        token = uuid4()
        try:
            rows = await self._calls.call(
                "claim_read_model",
                CLAIM,
                READ_MODEL_PROJECTION,
                generation,
                worker_id,
                str(token),
                lease_seconds,
                limit,
                expires_at=self._calls.attempt_deadline(expires_at),
            )
        except AccountingProtocolUnavailable as exc:
            if not outcome_may_be_ambiguous(exc):
                raise
            rows = await self._calls.call(
                "recover_read_model_claim",
                RECOVER_CLAIM,
                READ_MODEL_PROJECTION,
                generation,
                worker_id,
                str(token),
                limit,
                expires_at=expires_at,
            )
            if _claim_result(rows, generation, worker_id, token, limit) is None:
                raise exc
        return _claim_result(rows, generation, worker_id, token, limit)

    async def materialize(self, claim: ReadModelClaim, *, expires_at: float) -> int:
        # Freeze raw scalars again: a copied model can bypass its validators.
        claim = ReadModelClaim.model_validate(claim.model_dump())
        rows = await self._calls.call(
            "project_read_model",
            "SELECT deltallm_accounting_project_read_models($1,$2,$3::uuid,$4::integer,$5,$6::bigint[]) AS count",
            claim.generation,
            claim.worker_id,
            str(claim.lease_token),
            claim.accounting_partition,
            claim.after_sequence,
            list(claim.sequences),
            expires_at=expires_at,
        )
        if (
            len(rows) != 1
            or type(rows[0].get("count")) is not int
            or rows[0]["count"] not in (0, len(claim.sequences))
        ):
            raise invalid_result()
        return rows[0]["count"]

    async def progress(self, *, generation: int, expires_at: float) -> ReadModelProgress:
        _generation(generation)
        rows = await self._calls.call(
            "read_model_progress",
            PROGRESS,
            READ_MODEL_PROJECTION,
            generation,
            expires_at=expires_at,
        )
        if len(rows) != 1:
            raise invalid_result()
        try:
            value = ReadModelProgress.model_validate(rows[0])
        except ValueError:
            raise invalid_result() from None
        if value.generation != generation:
            raise invalid_result()
        return value


def _generation(generation: int) -> None:
    if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
        raise ValueError("read-model generation is invalid")


def _initialized(rows: Sequence[Mapping[str, object]], generation: int) -> bool:
    if len(rows) != 1 or type(rows[0].get("generation")) is not int:
        raise invalid_result()
    row = rows[0]
    if (
        row["generation"] != generation
        or type(row.get("partition_count")) is not int
        or not 1 <= row["partition_count"] <= 64
        or type(row.get("slots")) is not int
        or not 0 <= row["slots"] <= row["partition_count"]
    ):
        raise invalid_result()
    return row["slots"] == row["partition_count"]


def _claim_result(
    rows: Sequence[Mapping[str, object]],
    generation: int,
    worker_id: str,
    token: UUID,
    limit: int,
) -> ReadModelClaim | None:
    if len(rows) != 1 or type(rows[0].get("generation")) is not int:
        raise invalid_result()
    row = rows[0]
    if row["generation"] != generation:
        raise invalid_result()
    if row.get("accounting_partition") is None:
        if any(
            row.get(name) is not None for name in ("last_sequence", "sequences", "source_bytes")
        ):
            raise invalid_result()
        return None
    sequences = row.get("sequences")
    if not isinstance(sequences, (list, tuple)) or not 1 <= len(sequences) <= limit:
        raise invalid_result()
    try:
        return ReadModelClaim(
            generation=generation,
            worker_id=worker_id,
            lease_token=token,
            accounting_partition=row["accounting_partition"],
            after_sequence=row.get("last_sequence"),
            sequences=tuple(sequences),
            source_bytes=row.get("source_bytes"),
        )
    except ValueError:
        raise invalid_result() from None
