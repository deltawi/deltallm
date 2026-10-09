"""Resolve one provisional accounting debit from reviewed provider evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from src.billing.money import money_string
from src.config import DatabaseConnectionSettings
from src.db.runtime.allocation_config import DatabasePolicy
from src.db.runtime.client import PrismaClientManager


def _spend_payload(path: str | None, *, additional_charge: str) -> str:
    if path is None:
        if additional_charge != money_string(0):
            raise ValueError("positive additional charge requires --spend-payload-file")
        return "null"
    with Path(path).open("rb") as source:
        raw = source.read(262_145)
    if len(raw) > 262_144:
        raise ValueError("spend payload exceeds 256 KiB")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("spend payload must be a JSON object")
    if money_string(payload.get("cost_exact", payload.get("cost"))) != additional_charge:
        raise ValueError("spend payload cost must equal --additional-charge")
    payload["cost"] = additional_charge
    payload["cost_exact"] = additional_charge
    payload["spend_event_version"] = 2
    return json.dumps(payload, allow_nan=False, separators=(",", ":"), default=str)


async def reconcile(args: argparse.Namespace) -> None:
    operation_id = str(UUID(args.operation_id))
    additional_charge = money_string(args.additional_charge)
    spend_payload = _spend_payload(
        args.spend_payload_file,
        additional_charge=additional_charge,
    )
    reason = args.evidence_reason.strip()
    if not 1 <= len(reason) <= 256:
        raise ValueError("evidence reason must contain 1-256 characters")
    manager = PrismaClientManager()
    try:
        await manager.connect(
            DatabaseConnectionSettings(url=args.database_url, pool_size=1, pool_timeout=5),
            policy=DatabasePolicy("control", 1, 0.1, 2, 0.5, 5),
        )
        rows = await manager.client.query_raw(
            "SELECT deltallm_accounting_resolve_provisional("
            "$1,$2,$3::numeric,$4::jsonb,$5) AS event_sequence",
            args.generation,
            operation_id,
            additional_charge,
            spend_payload,
            reason,
        )
        print(
            json.dumps(
                {
                    "operation_id": operation_id,
                    "additional_charge_exact": additional_charge,
                    "event_sequence": int(rows[0]["event_sequence"]),
                },
                sort_keys=True,
            )
        )
    finally:
        await manager.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--additional-charge", default="0")
    parser.add_argument("--spend-payload-file")
    parser.add_argument("--evidence-reason", required=True)
    parser.add_argument("--provider-evidence-reviewed", action="store_true", required=True)
    args = parser.parse_args()
    if not 1 <= args.generation <= 2**63 - 1:
        parser.error("--generation must be a positive bigint")
    asyncio.run(reconcile(args))


if __name__ == "__main__":
    main()
