"""Resolve one provisional debit or release one unbilled grant hold."""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID

from src.billing.money import canonical_money, money_string
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
    reason = args.evidence_reason.strip()
    if not 1 <= len(reason) <= 256:
        raise ValueError("evidence reason must contain 1-256 characters")
    manager = PrismaClientManager()
    try:
        await manager.connect(
            DatabaseConnectionSettings(url=args.database_url, pool_size=1, pool_timeout=5),
            policy=DatabasePolicy("control", 1, 0.1, 2, 0.5, 5),
        )
        if args.grant_id is not None:
            result = await _release_grant(manager, args, reason)
        else:
            result = await _resolve_operation(manager, args, reason)
        print(json.dumps(result, sort_keys=True))
    finally:
        await manager.disconnect()


async def _release_grant(
    manager: PrismaClientManager, args: argparse.Namespace, reason: str
) -> dict[str, str]:
    hold = canonical_money(args.expected_grant_hold)
    if hold != Decimal(args.expected_grant_hold) or hold <= 0 or not 1 <= len(args.grant_id) <= 256:
        raise ValueError("grant release requires its ID and a positive expected hold")
    rows = await manager.client.query_raw(
        "SELECT deltallm_accounting_release_grant_hold($1,$2,$3::numeric,$4)::text AS released_exact",
        args.generation,
        args.grant_id,
        money_string(hold),
        reason,
    )
    return {"grant_id": args.grant_id, "released_exact": money_string(rows[0]["released_exact"])}


async def _resolve_operation(
    manager: PrismaClientManager, args: argparse.Namespace, reason: str
) -> dict[str, str | int]:
    operation_id = str(UUID(args.operation_id))
    additional_charge = money_string(args.additional_charge)
    spend_payload = _spend_payload(args.spend_payload_file, additional_charge=additional_charge)
    rows = await manager.client.query_raw(
        "SELECT deltallm_accounting_resolve_provisional($1,$2,$3::numeric,$4::jsonb,$5) AS event_sequence",
        args.generation,
        operation_id,
        additional_charge,
        spend_payload,
        reason,
    )
    return {
        "operation_id": operation_id,
        "additional_charge_exact": additional_charge,
        "event_sequence": int(rows[0]["event_sequence"]),
    }


def _expected_hold(value: str) -> str:
    try:
        hold = canonical_money(value)
        exact = hold == Decimal(value) and hold > 0
    except (ArithmeticError, TypeError, ValueError):
        exact = False
    if not exact:
        raise argparse.ArgumentTypeError("must be a positive exact money amount")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--generation", type=int, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--operation-id")
    target.add_argument("--grant-id")
    parser.add_argument("--expected-grant-hold", type=_expected_hold)
    parser.add_argument("--additional-charge", default="0")
    parser.add_argument("--spend-payload-file")
    parser.add_argument("--evidence-reason", required=True)
    parser.add_argument("--provider-evidence-reviewed", action="store_true", required=True)
    args = parser.parse_args()
    if not 1 <= args.generation <= 2**63 - 1:
        parser.error("--generation must be a positive bigint")
    if args.grant_id is not None:
        if args.expected_grant_hold is None:
            parser.error("--grant-id requires --expected-grant-hold")
        if args.additional_charge != "0" or args.spend_payload_file is not None:
            parser.error("--grant-id only releases an unbilled hold")
    elif args.expected_grant_hold is not None:
        parser.error("--expected-grant-hold requires --grant-id")
    asyncio.run(reconcile(args))


if __name__ == "__main__":
    main()
