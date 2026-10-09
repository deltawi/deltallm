"""Run bounded recovery actions on the role's existing native allocation."""

from __future__ import annotations

from src.billing.accounting.journal.accounting_recovery import RecoveryAction
from src.db.accounting.accounting_calls import AccountingDatabaseCalls, AccountingQueryClient
from src.db.accounting.permits.accounting_permit_results import invalid_result


_QUERIES = {
    RecoveryAction.EXPIRED_GRANTS: "SELECT deltallm_accounting_reconcile_expired_grants($1,$2::integer) AS count",
    RecoveryAction.EXPIRED_OPERATIONS: "SELECT deltallm_accounting_reconcile_expired($1,$2::integer) AS count",
    RecoveryAction.SETTLE_GRANTS: "SELECT deltallm_accounting_reconcile_grants($1,$2::integer) AS count",
    RecoveryAction.ROLL_WINDOWS: "SELECT deltallm_accounting_roll_windows($1,$2::integer) AS count",
}


class AccountingRecoveryRepository:
    def __init__(
        self, db: AccountingQueryClient, *, statement_budget_seconds: float = 0.25
    ) -> None:
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)

    async def recover(
        self, action: RecoveryAction, *, generation: int, limit: int, expires_at: float
    ) -> int:
        if (
            type(action) is not RecoveryAction
            or type(generation) is not int
            or not 1 <= generation <= 2**63 - 1
            or type(limit) is not int
            or not 1 <= limit <= 256
        ):
            raise ValueError("accounting recovery request is invalid")
        rows = await self._calls.call(
            "recovery_" + action.value, _QUERIES[action], generation, limit, expires_at=expires_at
        )
        if len(rows) != 1:
            raise invalid_result()
        count = rows[0].get("count")
        if type(count) is not int or not 0 <= count <= limit:
            raise invalid_result()
        return count
