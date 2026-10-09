"""Apply native balances only after the endpoint has authorized its rows."""

from collections.abc import Sequence
from typing import Any
import asyncio

from fastapi import HTTPException, Request

from src.billing.accounting.accounting_protocol import AccountingScope
from src.billing.budgets.budget import BudgetStateUnavailable
from src.db.accounting_budget_reads import AccountingBudgetReadRepository

_IDENTITIES = {
    AccountingScope.API_KEY: "token",
    AccountingScope.USER: "user_id",
    AccountingScope.TEAM: "team_id",
    AccountingScope.ORGANIZATION: "organization_id",
}


async def apply_accounting_balances(
    request: Request, rows: Sequence[dict[str, Any]], scope: AccountingScope
) -> None:
    repository = getattr(request.app.state, "accounting_budget_reads", None)
    if repository is None or not rows:
        return
    if not isinstance(repository, AccountingBudgetReadRepository):
        raise RuntimeError("accounting budget read owner is invalid")
    identity = _IDENTITIES[scope]
    ids = list(dict.fromkeys(str(row[identity]) for row in rows))
    try:
        values = {}
        async with asyncio.timeout(2.5):
            # Legacy organization/team listings are not paged. Each read is
            # bounded, and the complete response still has one time budget.
            for offset in range(0, len(ids), 500):
                values.update(await repository.balances(scope, ids[offset : offset + 500]))
    except (BudgetStateUnavailable, TimeoutError):
        raise HTTPException(
            status_code=503, detail="Budget balances are temporarily unavailable"
        ) from None
    for row in rows:
        value = values[str(row[identity])]
        # Existing admin contracts expose money as JSON numbers.
        row["spend"] = float(value.spend)
        if "budget_reset_at" in row:
            row["budget_reset_at"] = value.reset_at
