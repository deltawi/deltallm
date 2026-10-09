"""Read at most 64 partition counters and one oldest retained-work key."""

from __future__ import annotations

from src.billing.accounting.health.accounting_health import AccountingBacklogSnapshot
from src.db.accounting_calls import AccountingDatabaseCalls, AccountingQueryClient
from src.db.accounting_permit_results import invalid_result


BACKLOG_SQL = """
SELECT * FROM deltallm_accounting_backlog_snapshot($1)
"""


class AccountingBacklogRepository:
    def __init__(
        self, db: AccountingQueryClient, *, statement_budget_seconds: float = 0.25
    ) -> None:
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)

    async def snapshot(self, *, generation: int, expires_at: float) -> AccountingBacklogSnapshot:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("accounting backlog generation is invalid")
        rows = await self._calls.call(
            "terminal_backlog_snapshot", BACKLOG_SQL, generation, expires_at=expires_at
        )
        if len(rows) != 1:
            raise invalid_result()
        row = dict(rows[0])
        covered = row.pop("covered_partitions", None)
        expected = row.pop("expected_covered_partitions", None)
        if (
            type(covered) is not int
            or type(expected) is not int
            or covered != row.get("partition_count")
            or expected != covered
        ):
            raise invalid_result()
        try:
            value = AccountingBacklogSnapshot.model_validate(row)
        except ValueError:
            raise invalid_result() from None
        if value.generation != generation:
            raise invalid_result()
        return value
