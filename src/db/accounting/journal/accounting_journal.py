"""Append one bounded terminal batch and recover its exact durable identity."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json

from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.journal.accounting_journal import (
    JournalReceipt,
    TerminalJournalBatch,
    journal_batch,
)
from src.billing.accounting.journal.accounting_terminal_receipts import TerminalIdentity
from src.billing.accounting.journal.accounting_terminal_snapshots import LocalTerminalValue
from src.db.accounting.accounting_batches import result_rows, with_recovery
from src.db.accounting.accounting_calls import AccountingDatabaseCalls, AccountingQueryClient
from src.db.accounting.permits.accounting_permit_results import invalid_result


class AccountingJournalRepository:
    def __init__(
        self, db: AccountingQueryClient, *, statement_budget_seconds: float = 0.25
    ) -> None:
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)

    async def append_batch(
        self, values: Sequence[LocalTerminalValue], *, expires_at: float
    ) -> tuple[JournalReceipt, ...]:
        if not values:
            return ()
        batch = journal_batch(values)

        async def attempt(deadline: float) -> dict[str, JournalReceipt]:
            rows = await self._calls.call(
                "append_terminal_journal",
                "SELECT * FROM deltallm_accounting_append_terminal_journal("
                "$1,$2::jsonb,$3::text[],$4::text[])",
                batch.generation,
                batch.compact,
                list(batch.reservations),
                list(batch.finalizations),
                expires_at=deadline,
            )
            return _receipts(batch, result_rows(rows, "operation_id", batch.keys))

        results, recovered = await with_recovery(
            batch.keys,
            attempt,
            lambda deadline: self._recover(batch, expires_at=deadline),
            calls=self._calls,
            expires_at=expires_at,
        )
        for key, receipt in recovered.items():
            if results[key].model_copy(update={"replayed": True}) != receipt:
                raise invalid_result()
            results[key] = receipt
        return tuple(results[key] for key in batch.keys)

    async def find_batch(
        self, values: Sequence[TerminalIdentity], *, generation: int, expires_at: float
    ) -> tuple[JournalReceipt | None, ...]:
        """Read durable terminals for exact issues; a missing or other issue is None."""
        if not values:
            return ()
        if len(values) > 256:
            raise ValueError("terminal lookup exceeds its entry limit")
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("terminal lookup generation is invalid")
        keys = [str(value.operation_id) for value in values]
        if len(set(keys)) != len(keys):
            raise ValueError("terminal lookup repeats an operation")
        payload = json.dumps(
            [value.model_dump(mode="json") for value in values],
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        # One unique-index probe per operation. The issue fields must match, so a
        # row written for another issue of the same operation never releases this one.
        rows = await self._calls.call(
            "find_terminal_journal",
            "SELECT j.operation_id,j.sequence AS journal_sequence,j.outcome "
            "FROM jsonb_array_elements($1::jsonb) v CROSS JOIN LATERAL "
            "(SELECT j.* FROM deltallm_accounting_terminal_journal j "
            "WHERE j.operation_id=v->>'operation_id' OFFSET 0) j "
            "WHERE j.generation=$2 AND j.grant_id=v->>'grant_id' "
            "AND j.grantee_id=v->>'grantee_id' AND j.fence_token=(v->>'fence_token')::uuid "
            "AND j.permit_ordinal=(v->>'permit_ordinal')::integer "
            "AND j.reservation_sha256=decode(v->>'reservation_sha256','hex')",
            payload,
            generation,
            expires_at=expires_at,
        )
        by_key: dict[str, JournalReceipt] = {}
        for row in rows:
            key = row.get("operation_id")
            sequence = row.get("journal_sequence")
            outcome = row.get("outcome")
            if (
                type(key) is not str
                or key not in keys
                or key in by_key
                or type(sequence) is not int
                or outcome not in {item.value for item in AccountingOutcome}
            ):
                raise invalid_result()
            try:
                by_key[key] = JournalReceipt(
                    protocol_generation=generation,
                    operation_id=values[keys.index(key)].operation_id,
                    journal_sequence=sequence,
                    outcome=AccountingOutcome(outcome),
                    replayed=True,
                )
            except ValueError:
                raise invalid_result() from None
        return tuple(by_key.get(key) for key in keys)

    async def _recover(
        self, batch: TerminalJournalBatch, *, expires_at: float
    ) -> dict[str, JournalReceipt]:
        rows = await self._calls.call(
            "recover_terminal_journal",
            "SELECT j.operation_id,j.sequence AS journal_sequence,j.outcome,TRUE AS replayed,"
            "(j.generation=$2 AND j.grant_id=v->>'grant_id' "
            "AND j.grantee_id=v->>'grantee_id' AND j.fence_token=(v->>'fence_token')::uuid "
            "AND j.permit_ordinal=(v->>'permit_ordinal')::integer "
            "AND j.allowance_exact=(v->>'allowance_exact')::numeric "
            "AND j.reservation_sha256=decode(v->>'reservation_sha256','hex') "
            "AND j.finalization_sha256=decode(v->>'finalization_sha256','hex')) AS identity_matches "
            "FROM jsonb_array_elements($1::jsonb) v CROSS JOIN LATERAL "
            "(SELECT j.* FROM deltallm_accounting_terminal_journal j "
            "WHERE j.operation_id=v->>'operation_id' OFFSET 0) j",
            batch.compact,
            batch.generation,
            expires_at=expires_at,
        )
        by_key = {str(row.get("operation_id")): row for row in rows}
        if (
            len(by_key) != len(rows)
            or not set(by_key).issubset(batch.keys)
            or any(row.get("identity_matches") is not True for row in rows)
        ):
            raise invalid_result()
        return _receipts(batch, by_key)


def _receipts(
    batch: TerminalJournalBatch, rows: Mapping[str, Mapping[str, object]]
) -> dict[str, JournalReceipt]:
    result = {}
    for value in batch.values:
        key = str(value.operation_id)
        if key not in rows:
            continue
        row = rows[key]
        if (
            row.get("operation_id") != key
            or row.get("outcome") != value.outcome.value
            or type(row.get("journal_sequence")) is not int
            or type(row.get("replayed")) is not bool
        ):
            raise invalid_result()
        try:
            result[key] = JournalReceipt(
                protocol_generation=batch.generation,
                operation_id=value.operation_id,
                journal_sequence=row["journal_sequence"],
                outcome=value.outcome,
                replayed=row["replayed"],
            )
        except ValueError:
            raise invalid_result() from None
    return result
