"""Measure proof copies without a network, database, or connection-pool change."""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import json
from statistics import median
from time import process_time

from src.billing.accounting.permits.accounting_local_leases import (
    LocalAccountingHandle,
    LocalDispatchPermit,
)
from src.billing.accounting.permits.accounting_local_receipts import RetainedLocalReceipt
from src.billing.accounting.accounting_protocol import AccountingAttempt, ReserveDecision
from src.billing.accounting.journal.accounting_terminal_snapshots import (
    FrozenLocalTerminal,
    freeze_terminal_snapshots,
)
from tests.test_accounting_local_leases import terminal


def cycle(value):
    retained = RetainedLocalReceipt.freeze(value.receipt)
    proof = retained.restore()
    item = proof.reservation
    permit = LocalDispatchPermit(
        protocol_generation=item.protocol_generation,
        operation_id=item.operation_id,
        decision=ReserveDecision.DISPATCH,
        dispatch_token=item.owner_token,
        accounting_partition=proof.grant.accounting_partition,
        proof=proof,
    )
    attempt = AccountingAttempt(
        deployment_id="deployment", provider="openai", model="model", pricing_snapshot={}
    )
    handle = LocalAccountingHandle(
        reservation=item,
        dispatch_token=permit.dispatch_token,
        accounting_partition=permit.accounting_partition,
        proof=permit.proof,
        attempts=(attempt,),
    )
    # Provider retries must retain the first issue, not create another reservation.
    retried = handle.model_copy(update={"attempts": (*handle.attempts, attempt)})
    snapshot = FrozenLocalTerminal(
        value.model_copy(update={"receipt": retried.proof}), generation=7
    )
    assert freeze_terminal_snapshots((snapshot,), generation=7)[0] is snapshot
    assert snapshot.reservation_json == retained.reservation_json


async def measure(iterations: int, repeats: int) -> dict:
    results = {}
    for name, width in (("small", 0), ("large", 1200)):
        value = terminal()
        if width:
            value.receipt.reservation.pricing_snapshot["wide"] = {str(i): i for i in range(width)}
            value.receipt.reservation.audit_envelope["wide"] = {
                str(i): [i, i] for i in range(width)
            }
        cycle(value)
        samples = []
        for _ in range(repeats):
            started = process_time()
            for _ in range(iterations):
                cycle(value)
            samples.append((process_time() - started) * 1_000_000 / iterations)
        profile = cProfile.Profile()
        profile.runcall(cycle, value)
        calls = {}
        for entry in profile.getstats():
            code = entry.code
            if not isinstance(code, str) and code.co_name in (
                "model_validate",
                "model_validate_json",
                "model_dump",
                "dumps",
                "loads",
            ):
                key = code.co_name
                calls[key] = calls.get(key, 0) + entry.callcount
        results[name] = {
            "median_cpu_microseconds": median(samples),
            "samples_cpu_microseconds": samples,
            "calls_per_cycle": calls,
        }
    return {"iterations": iterations, "repeats": repeats, "cases": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10_000 or not 1 <= args.repeats <= 20:
        parser.error("Use 1–10000 iterations and 1–20 repeats")
    print(json.dumps(asyncio.run(measure(args.iterations, args.repeats)), indent=2))


if __name__ == "__main__":
    main()
