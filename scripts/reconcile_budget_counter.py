"""Install an operator-verified complete team/model total during maintenance."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from src.config import DatabaseConnectionSettings
from src.db.allocation_config import DatabasePolicy
from src.db.budget_reconciliation import BudgetReconciliationRepository
from src.db.client import PrismaClientManager


async def reconcile(args: argparse.Namespace) -> None:
    manager = PrismaClientManager()
    try:
        await manager.connect(
            DatabaseConnectionSettings(url=args.database_url),
            policy=DatabasePolicy("control", 1, 0.1, 2, 0.2, 3),
        )
        updated_at = args.expected_updated_at
        if updated_at is not None:
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                raise ValueError("expected timestamp must include a UTC offset")
            updated_at = updated_at.astimezone(UTC).replace(tzinfo=None)
        await BudgetReconciliationRepository(manager.client).repair(
            team_id=args.team_id,
            model=args.model,
            verified_total=Decimal(args.verified_total),
            expected_spend=Decimal(args.expected_spend)
            if args.expected_spend is not None
            else None,
            expected_updated_at=updated_at,
        )
        print("Counter reconciled; retain the reviewed source totals with the maintenance record.")
    finally:
        await manager.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--verified-total", required=True, help="Complete total including archived spend"
    )
    parser.add_argument(
        "--expected-spend", help="Current stored total; omit only for an absent row"
    )
    parser.add_argument("--expected-updated-at", help="Current row timestamp with UTC offset")
    parser.add_argument(
        "--writers-paused-and-drained",
        action="store_true",
        required=True,
        help="Confirm inference and all spend/selector writers are paused and drained",
    )
    args = parser.parse_args()
    if (args.expected_spend is None) != (args.expected_updated_at is None):
        parser.error("both expected fields are required for an existing counter")
    asyncio.run(reconcile(args))


if __name__ == "__main__":
    main()
