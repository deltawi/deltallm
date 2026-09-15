"""Apply reviewed provider evidence to an unknown operation; never resends a request."""

import argparse
import asyncio
from pathlib import Path

from src.billing.spend_reconciliation import SpendOperationResolution
from src.config import DatabaseConnectionSettings
from src.db.allocation_config import DatabasePolicy
from src.db.client import PrismaClientManager
from src.db.spend_reconciliation import SpendReconciliationRepository


async def reconcile(args: argparse.Namespace) -> None:
    with Path(args.evidence_file).open("rb") as source:
        data = source.read(8193)
    if len(data) > 8192:
        raise ValueError("Evidence file exceeds 8 KiB")
    resolution = SpendOperationResolution.model_validate_json(data)
    manager = PrismaClientManager()
    try:
        await manager.connect(
            DatabaseConnectionSettings(url=args.database_url),
            policy=DatabasePolicy("control", 1, 0.1, 1, 0.2, 2),
        )
        await SpendReconciliationRepository(manager.client).reconcile(
            resolution,
            expires_at=asyncio.get_running_loop().time() + 0.25,
        )
        print("Reconciliation and operator audit committed; spend worker will settle the receipt.")
    finally:
        await manager.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--provider-evidence-reviewed", action="store_true", required=True)
    asyncio.run(reconcile(parser.parse_args()))


if __name__ == "__main__":
    main()
